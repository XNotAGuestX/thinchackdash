"""Generate unreviewed mapping files from Datadog's OTel mapping table for every integration without one.

Development-time only; the converter never imports this file. No model or heuristic picks a
target: an entry is written only when Datadog's OpenTelemetry Metrics Mapping table has exactly one
row for the metric, the OTel side is defined in the collector-contrib / semantic-convention /
JMX sources, and the #106 kind rule fits that OTel type. Everything else is reported as missing
with its reason.

Inputs (not committed; see the coverage report header for the pinned versions):
  --integrations-core  checkout of DataDog/integrations-core at SHA (metadata.csv, assets/dashboards)
  --otel-contrib       checkout of open-telemetry/opentelemetry-collector-contrib (receiver metadata.yaml)
  --page               saved https://docs.datadoghq.com/opentelemetry/mapping/metrics_mapping.md
  --fetched            date the page was saved (YYYY-MM-DD)

Outputs:
  mappings/datadog/generated/<integration>.json   one file per integration with >= 1 entry;
      outside the default `--mapping mappings/datadog` table (non-recursive), opt in with
      `--mapping mappings/datadog --mapping mappings/datadog/generated`. `reviewed` stays null.
  docs/INTEGRATION_COVERAGE.md                    coverage per integration, worst first, with
      importance from scripts/integration_importance.yaml.

Usage:
  uv run scripts/generate_vendor_mappings.py --integrations-core IC --otel-contrib OCC \
      --page metrics_mapping.md --fetched YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pin_otel_mapping  # noqa: E402

from migrator.source.datadog.mapping import parse_mapping, parse_mappings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAPPINGS = ROOT / "mappings" / "datadog"
OUT_DIR = MAPPINGS / "generated"
REPORT = ROOT / "docs" / "INTEGRATION_COVERAGE.md"
IMPORTANCE = ROOT / "scripts" / "integration_importance.yaml"
CATALOG = ROOT / "fixtures" / "otel" / "catalog.yaml"
SHA = "a9b9e42b7e484861ecbd1f5919da7c52a694b74d"
OTEL_TAG = "v0.165.0"

# integrations-core directories that already have a hand-evidenced file in mappings/datadog/.
TRACKED = {
    "docker_daemon": "docker.json",
    "kafka": "kafka.json",
    "kafka_consumer": "kafka.json",
    "nginx": "nginx.json",
    "postgres": "postgres.json",
    "redisdb": "redis.json",
}
FILE_LABELS = {"host": "host_name"}
LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
SCALE = re.compile(r"^× (\S+)$")
QUERY_METRIC = re.compile(r"([A-Za-z_][\w.]*\.[\w.]+)\{")


@dataclass(frozen=True)
class DdType:
    metric_type: str
    unit: str
    per_unit: str


@dataclass
class Result:
    integration: str
    importance: int
    metrics: list[str]
    dashboard_metrics: set[str]
    mapped: set[str] = field(default_factory=set)
    blocked: dict[str, str] = field(default_factory=dict)  # metric -> reason (vendor row exists)
    tracked: bool = False
    has_file: bool = False

    @property
    def missing(self) -> int:
        return len(self.metrics) - len(self.mapped)

    @property
    def coverage(self) -> float:
        return len(self.mapped) / len(self.metrics) if self.metrics else 0.0


# --------------------------------------------------------------------------- inputs


def load_catalog(occ: Path) -> dict[str, dict[str, Any]]:
    """Repo catalog (semantic conventions, JMX, pinned receivers) plus every receiver in `occ`."""
    catalog: dict[str, dict[str, Any]] = yaml.safe_load(CATALOG.read_text())
    for path in sorted(occ.rglob("metadata.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        source = f"collector-contrib@{OTEL_TAG}/{path.relative_to(occ)}"
        for name, m in sorted((doc.get("metrics") or {}).items()):
            if "sum" in m:
                kind, mono = "sum", bool(m["sum"].get("monotonic"))
            elif "histogram" in m:
                kind, mono = "histogram", None
            else:
                kind, mono = "gauge", None
            entry = catalog.setdefault(
                name, {"type": kind, "monotonic": mono, "unit": m.get("unit"), "sources": []}
            )
            if entry["type"] != kind:
                entry.setdefault("conflicts", []).append(source)
            entry["sources"] = sorted(set(entry["sources"]) | {source})
    return catalog


def load_types(ic: Path) -> dict[str, dict[str, DdType]]:
    out: dict[str, dict[str, DdType]] = {}
    for path in sorted(ic.glob("*/metadata.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            rows = {
                r["metric_name"]: DdType(r["metric_type"], r["unit_name"], r["per_unit_name"])
                for r in csv.DictReader(f)
                if r.get("metric_name")
            }
        if rows:
            out[path.parent.name] = dict(sorted(rows.items()))
    return out


def dashboard_metrics(ic: Path, integration: str) -> set[str]:
    found: set[str] = set()
    for path in sorted((ic / integration / "assets" / "dashboards").glob("*.json")):
        found |= set(QUERY_METRIC.findall(path.read_text(encoding="utf-8")))
    return found


def existing_mapped() -> set[str]:
    names: set[str] = set()
    for path in sorted(MAPPINGS.glob("*.json")):
        names |= set(json.loads(path.read_text())["metrics"])
    return names


# --------------------------------------------------------------------------- rules


def expected_kind(t: DdType) -> str | None:
    """Same rule as tests/datadog/test_metric_types.py (issue #106)."""
    if t.metric_type in ("rate", "count"):
        return "rate"
    if t.metric_type == "gauge":
        return "rate" if t.per_unit == "second" else "gauge"
    return None


def build_entry(
    name: str, t: DdType, row: dict[str, Any], catalog: dict[str, Any], integration: str, fetched: str
) -> dict[str, Any] | str:
    """A mapping entry, or the reason none can be generated."""
    otel = catalog.get(row["otel"])
    if otel is None:
        return "OTel metric not in pinned definitions"
    if otel.get("conflicts"):
        return "OTel metric type conflicts between sources"
    if otel["type"] == "histogram":
        return "OTel target is a histogram"
    flt: dict[str, str] = row["filter"]
    if any(not LABEL.match(k) for k in flt):
        return "filter attribute is not a plain label name"
    scale = 1.0
    if row["transform"]:
        m = SCALE.match(row["transform"])
        if not m:
            return "vendor transform renames an attribute"
        try:
            scale = float(m.group(1))
        except ValueError:
            return "vendor scale is not a number"
        if scale <= 0:
            return "vendor scale is not positive"
    kind = expected_kind(t)
    if kind is None:
        return f"Datadog type {t.metric_type!r} has no kind rule"
    if kind == "rate" and not (otel["type"] == "sum" and otel["monotonic"]):
        return "Datadog rate/count but OTel target is not a monotonic sum"
    source_unit = t.unit + (f"/{t.per_unit}" if t.per_unit else "")
    target_unit = otel.get("unit") or ""
    vendor = f"{name} -> {row['otel']}"
    if flt:
        vendor += " filter " + ", ".join(f"{k}={v}" for k, v in sorted(flt.items()))
    if row["transform"]:
        vendor += f" transform {row['transform']}"
    otel_type = otel["type"] + (" (monotonic)" if otel["monotonic"] else "")
    ref = (
        "GENERATED by scripts/generate_vendor_mappings.py; not reviewed. "
        f"Vendor row: Datadog OTel Metrics Mapping table (fetched {fetched}) {vendor}. "
        f"Datadog: integrations-core@{SHA[:7]} {integration}/metadata.csv {t.metric_type}"
        f"{' per ' + t.per_unit if t.per_unit else ''}, unit {t.unit or 'none'}. "
        f"OTel: {', '.join(otel['sources'])} {otel_type}, unit {target_unit or 'none'}. "
        f"Kind {kind} by the #106 rule. Units, labels and grain not checked."
    )
    entry: dict[str, Any] = {
        "kind": kind,
        "ref": ref,
        "reviewed": None,
        "scale": int(scale) if scale.is_integer() else scale,
        "sourceUnit": source_unit,
        "target": {"name": row["otel"], "type": otel["type"]},
        "targetUnit": target_unit,
    }
    if flt:
        entry["matchers"] = dict(sorted(flt.items()))
    return entry


# --------------------------------------------------------------------------- run


def generate(ic: Path, occ: Path, page: Path, fetched: str) -> tuple[list[Result], dict[str, dict]]:
    rows = pin_otel_mapping.parse_table(page.read_text(encoding="utf-8"), ("",))
    by_dd: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = {k: row[k] for k in ("otel", "filter", "transform")}
        bucket = by_dd.setdefault(row["datadog"], [])
        if key not in bucket:
            bucket.append(key)
    catalog = load_catalog(occ)
    types = load_types(ic)
    importance: dict[str, int] = yaml.safe_load(IMPORTANCE.read_text())
    taken = existing_mapped()
    results: list[Result] = []
    files: dict[str, dict] = {}
    for integration, metrics in types.items():
        res = Result(
            integration,
            importance.get(integration, 0),
            list(metrics),
            dashboard_metrics(ic, integration) & set(metrics),
            tracked=integration in TRACKED,
        )
        results.append(res)
        if res.tracked:
            res.mapped = set(metrics) & taken
            continue
        entries: dict[str, Any] = {}
        elsewhere: set[str] = set()  # metadata.csv files overlap; the first file owns a metric
        for name, t in metrics.items():
            vendor = by_dd.get(name, [])
            if name in taken:
                elsewhere.add(name)
                continue
            if not vendor:
                continue
            if len(vendor) > 1:
                res.blocked[name] = "several vendor rows (ambiguous)"
                continue
            built = build_entry(name, t, vendor[0], catalog, integration, fetched)
            if isinstance(built, str):
                res.blocked[name] = built
                continue
            entries[name] = built
        res.mapped = set(entries) | elsewhere
        taken |= set(entries)
        if entries:
            res.has_file = True
            files[integration] = {
                "labels": FILE_LABELS,
                "metrics": entries,
                "source": "datadog",
                "version": 1,
            }
    return results, files


def validate(files: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    for name, raw in files.items():
        parsed = parse_mapping(raw)
        if not parsed.ok:
            errors += [f"{name}: {e}" for e in parsed.errors]
    combined = [(p.name, json.loads(p.read_text())) for p in sorted(MAPPINGS.glob("*.json"))]
    combined += [(f"generated/{n}.json", raw) for n, raw in files.items()]
    merged = parse_mappings(combined)
    if not merged.ok:
        errors += merged.errors
    return errors


def _row(r: Result) -> str:
    dash = len(r.dashboard_metrics)
    dash_mapped = len(r.dashboard_metrics & r.mapped)
    dash_cell = f"{dash_mapped}/{dash}" if dash else "–"
    origin = "hand" if r.tracked else ("generated" if r.has_file else "none")
    return (
        f"| {r.integration} | {r.importance} | {len(r.metrics)} | {len(r.mapped)} | {r.missing} | "
        f"{r.coverage:.0%} | {dash_cell} | {len(r.blocked)} | {origin} |"
    )


HEAD = (
    "| Integration | Importance | Metrics | Mapped | Missing | Coverage | Dashboard metrics mapped "
    "| Vendor row, blocked | File |\n|---|---|---|---|---|---|---|---|---|"
)


def render_report(results: list[Result], fetched: str) -> str:
    untracked = [r for r in results if not r.tracked]
    worst = sorted(untracked, key=lambda r: (r.coverage, -r.importance, -r.missing, r.integration))
    by_importance = sorted(
        untracked, key=lambda r: (-r.importance, r.coverage, -r.missing, r.integration)
    )
    reasons = Counter(reason for r in untracked for reason in r.blocked.values())
    generated = [r for r in untracked if r.has_file]
    total = sum(len(r.metrics) for r in untracked)
    mapped = sum(len(r.mapped) for r in untracked)
    imp = Counter(r.importance for r in untracked)
    out = [
        "# Integration mapping coverage",
        "",
        "Generated by `scripts/generate_vendor_mappings.py`; do not edit by hand. Inputs:",
        f"[DataDog/integrations-core@{SHA[:7]}](https://github.com/DataDog/integrations-core/tree/{SHA})"
        " `*/metadata.csv` and `*/assets/dashboards`,",
        f"opentelemetry-collector-contrib {OTEL_TAG} receiver `metadata.yaml`, `fixtures/otel/catalog.yaml`,",
        f"and Datadog's OTel Metrics Mapping table fetched {fetched}.",
        "",
        "A metric counts as mapped only when the vendor table has exactly one row for it and the row",
        "passes the deterministic checks (OTel type known, #106 kind rule fits, scale-only transform),",
        "or when another mapping file already maps it (`metadata.csv` files overlap).",
        "Generated entries are unreviewed and live in `mappings/datadog/generated/`, outside the default",
        "table. Importance (1 niche … 5 ubiquitous) is a hand-set estimate in",
        "`scripts/integration_importance.yaml`.",
        "",
        "## Summary",
        "",
        f"- Integrations with a `metadata.csv`: {len(results)}"
        f" ({len(results) - len(untracked)} already hand-mapped, {len(untracked)} untracked).",
        f"- Untracked integrations with a generated file: {len(generated)}; with no mapped metric:"
        f" {len(untracked) - len(generated)}.",
        f"- Untracked metrics: {total}; mapped: {mapped} ({mapped / total:.1%}).",
        "- Untracked integrations by importance: "
        + ", ".join(f"{k}: {imp[k]}" for k in sorted(imp, reverse=True))
        + ".",
        "",
        "Vendor rows that exist but could not be turned into an entry:",
        "",
        "| Reason | Metrics |",
        "|---|---|",
        *[f"| {reason} | {n} |" for reason, n in sorted(reasons.items(), key=lambda x: (-x[1], x[0]))],
        "",
        "## Worst-covered by importance",
        "",
        "Most important first, then lowest coverage: the integrations whose gaps matter most.",
        "",
        HEAD,
        *[_row(r) for r in by_importance if r.importance >= 4],
        "",
        "## All untracked integrations, worst first",
        "",
        "Sorted by coverage, then importance (high first), then missing metrics.",
        "",
        HEAD,
        *[_row(r) for r in worst],
        "",
        "## Hand-mapped integrations (for comparison)",
        "",
        HEAD,
        *[_row(r) for r in sorted(results, key=lambda r: r.integration) if r.tracked],
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--integrations-core", type=Path, required=True)
    ap.add_argument("--otel-contrib", type=Path, required=True)
    ap.add_argument("--page", type=Path, required=True)
    ap.add_argument("--fetched", required=True)
    args = ap.parse_args()
    results, files = generate(args.integrations_core, args.otel_contrib, args.page, args.fetched)
    errors = validate(files)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    OUT_DIR.mkdir(exist_ok=True)
    for stale in OUT_DIR.glob("*.json"):
        stale.unlink()
    for name, raw in sorted(files.items()):
        (OUT_DIR / f"{name}.json").write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")
    REPORT.write_text(render_report(results, args.fetched))
    mapped = sum(len(f["metrics"]) for f in files.values())
    print(f"{len(files)} files, {mapped} entries -> {OUT_DIR.relative_to(ROOT)}; report {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
