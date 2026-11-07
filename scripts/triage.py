"""Combine verifier verdicts into a review priority per request (development time only, issue #87).

Reads the verdict files of scripts/verify_common.py (fixtures/oracle/verify/<key>/<verifier>.yaml) and
writes fixtures/expected/TRIAGE.md plus fixtures/expected/verified.json.
Triage only, never approval: a human sets `reviewed: true` in overrides.yaml (fixtures/expected/REVIEW.md).

Priority per request:
  red         any block (from any verifier)
  yellow      any nit, or judge and haiku both present and different
  green       every present verdict passes and judge or haiku is present
  unverified  otherwise
By default every verifier is trusted: the checkers themselves are not tested. With --catch-rates, the
mutation verdicts (fixtures/oracle/verify/mutations/) are read too, and a verifier whose catch rate is
measured (>= MIN_MUTATIONS known-bad variants) and below THRESHOLD is untrusted: its nit/block findings
still count, but its pass is not evidence for green.

Usage: uv run scripts/triage.py [--all | <svc>/<id> ...] [--out PATH] [--catch-rates]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_common as vc  # noqa: E402

ROOT = vc.ROOT
TRIAGE_MD = ROOT / "fixtures" / "expected" / "TRIAGE.md"
VERIFIED_JSON = ROOT / "fixtures" / "expected" / "verified.json"
THRESHOLD = 0.8
MIN_MUTATIONS = 10
PRIORITIES = ("red", "yellow", "unverified", "green")  # review order
LLM_VERIFIERS = ("judge", "haiku")
REASON_CHARS = 240


# --------------------------------------------------------------------------- catch rates


def _rate(caught: int, total: int) -> float | None:
    return caught / total if total else None


def catch_rates(keys: list[str]) -> dict[str, dict[str, Any]]:
    """Per verifier: how many known-bad mutations it caught (verdict != pass).

    {verifier: {caught, total, rate, by_mutation: {kind: {caught, total, rate}},
                structural_caught, structural_total, structural_rate}}
    Only verifiers with at least one mutation verdict appear. structural_rate is None when the
    verifier saw no structural mutation.
    """
    counts: dict[str, dict[str, Any]] = {}
    for key in keys:
        mutations = vc.load_mutations(key)
        if not mutations:
            continue
        for verifier in vc.VERIFIERS:
            got = vc.read_verdicts(key, verifier, mutations=True)
            if got is None:
                continue
            c = counts.setdefault(verifier, {"caught": 0, "total": 0, "by_mutation": {}, "structural_caught": 0, "structural_total": 0})
            for m in mutations:
                result = got.get(m["id"])
                if result is None:
                    continue
                caught = int(result["verdict"] != "pass")
                kind = m.get("mutation")
                kind = str(kind.get("kind") or kind.get("type") or kind) if isinstance(kind, dict) else str(kind)
                c["caught"] += caught
                c["total"] += 1
                bm = c["by_mutation"].setdefault(kind, {"caught": 0, "total": 0})
                bm["caught"] += caught
                bm["total"] += 1
                if m.get("structural"):
                    c["structural_caught"] += caught
                    c["structural_total"] += 1
    out: dict[str, dict[str, Any]] = {}
    for verifier in vc.VERIFIERS:
        if verifier not in counts or not counts[verifier]["total"]:
            continue
        c = counts[verifier]
        out[verifier] = {
            "caught": c["caught"],
            "total": c["total"],
            "rate": c["caught"] / c["total"],
            "by_mutation": {
                k: {**v, "rate": v["caught"] / v["total"]} for k, v in sorted(c["by_mutation"].items())
            },
            "structural_caught": c["structural_caught"],
            "structural_total": c["structural_total"],
            "structural_rate": _rate(c["structural_caught"], c["structural_total"]),
        }
    return out


def untrusted_verifiers(rates: dict[str, dict[str, Any]]) -> set[str]:
    return {v for v, r in rates.items() if r["total"] >= MIN_MUTATIONS and r["rate"] < THRESHOLD}


# --------------------------------------------------------------------------- priorities


def priority(signals: dict[str, str], untrusted: set[str] | frozenset[str] = frozenset()) -> str:
    """A finding (nit/block) counts from every verifier: a low catch rate means a verifier misses
    errors, not that what it reports is wrong. Only a trusted verifier's pass counts toward green."""
    verdicts = set(signals.values())
    if "block" in verdicts:
        return "red"
    trusted = {v: s for v, s in signals.items() if v not in untrusted}
    llm = [trusted[v] for v in LLM_VERIFIERS if v in trusted]
    if "nit" in verdicts or (len(llm) == 2 and llm[0] != llm[1]):
        return "yellow"
    if llm and verdicts == {"pass"}:
        return "green"
    return "unverified"


def priorities(
    key: str,
    *,
    rates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Every request of one dashboard, in case order:
    {source_path: {priority, signals: {verifier: verdict}, reasons: {verifier: [..]}, untrusted: [..]}}.
    Every verifier is trusted unless `rates` (catch_rates) is given."""
    untrusted = untrusted_verifiers(rates or {})
    files = {v: vc.read_verdicts(key, v) for v in vc.VERIFIERS}
    out: dict[str, dict[str, Any]] = {}
    for request in vc.load_case(key)["requests"]:
        sp = request["source_path"]
        signals: dict[str, str] = {}
        reasons: dict[str, list[str]] = {}
        for verifier, results in files.items():
            if results and sp in results:
                signals[verifier] = results[sp]["verdict"]
                if results[sp].get("reasons"):
                    reasons[verifier] = list(results[sp]["reasons"])
        out[sp] = {
            "priority": priority(signals, untrusted),
            "signals": signals,
            "reasons": reasons,
            "untrusted": sorted(v for v in signals if v in untrusted),
        }
    return out


# --------------------------------------------------------------------------- TRIAGE.md

HEADER = """\
<!-- GENERATED by scripts/triage.py from fixtures/oracle/verify/. Do not edit by hand. -->
# Expectation triage (verifier signals)

The verifiers (issue #87) check agent0-derived expectations. **Green** requests count as verified: they
are listed in `fixtures/expected/verified.json` and run by default. Humans verify as much as possible,
starting with red and yellow: accept or correct a request in
`fixtures/expected/<service>/<dashboard_id>/overrides.yaml` with `reviewed: true` (see
[REVIEW.md](REVIEW.md) and `tests/README.md`, section "Review"). A human `reviewed: true` always wins.

Priority: **red** = any verifier says block; **yellow** = a nit, or judge and haiku disagree;
**green** = every trusted verdict passes and judge or haiku checked the request; **unverified** =
otherwise. Only with `scripts/triage.py --catch-rates` are the verifiers themselves tested: a verifier
whose catch rate on known-bad mutations is measured (at least {min} mutations) and below {threshold:.0%} is
*untrusted*: its nit/block findings still count (a low catch rate means it misses errors, not that its
findings are wrong), but its pass is not evidence for green. Verdict files: `fixtures/oracle/verify/<service>/<dashboard_id>/<verifier>.yaml`.
"""


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= REASON_CHARS else text[: REASON_CHARS - 1] + "…"


def _code(text: str) -> str:
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def render(keys: list[str], rates: dict[str, dict[str, Any]]) -> str:
    untrusted = untrusted_verifiers(rates)
    lines = [HEADER.format(min=MIN_MUTATIONS, threshold=THRESHOLD)]
    per_key = {key: priorities(key, rates=rates) for key in keys}

    lines += ["## Summary", "", "| Dashboard | Requests | red | yellow | unverified | green | Verifiers present |", "|---|---|---|---|---|---|---|"]
    totals = dict.fromkeys(PRIORITIES, 0)
    for key, rows in per_key.items():
        counts = {p: sum(r["priority"] == p for r in rows.values()) for p in PRIORITIES}
        for p in PRIORITIES:
            totals[p] += counts[p]
        present = [v for v in vc.VERIFIERS if any(v in r["signals"] for r in rows.values())]
        names = ", ".join(f"{v} (untrusted)" if v in untrusted else v for v in present) or "none"
        lines.append(f"| [`{key}`](#{_anchor(key)}) | {len(rows)} | " + " | ".join(str(counts[p]) for p in PRIORITIES) + f" | {names} |")
    lines.append(f"| **Total** | **{sum(totals.values())}** | " + " | ".join(f"**{totals[p]}**" for p in PRIORITIES) + " | |")

    lines += ["", "## Verifier catch rates (known-bad mutations)", ""]
    if not rates:
        lines.append("Not measured (default; `--catch-rates` reads the mutation verdicts of scripts/mutate_expected.py): every verifier counts as trusted.")
    else:
        lines += ["| Verifier | Caught / total | Rate | Structural | Trusted |", "|---|---|---|---|---|"]
        for v, r in rates.items():
            structural = f"{r['structural_caught']} / {r['structural_total']} ({_pct(r['structural_rate'])})" if r["structural_total"] else "n/a"
            measured = r["total"] >= MIN_MUTATIONS
            trusted = "no" if v in untrusted else ("yes" if measured else f"yes (fewer than {MIN_MUTATIONS} mutations)")
            lines.append(f"| {v} | {r['caught']} / {r['total']} | {_pct(r['rate'])} | {structural} | {trusted} |")
        lines += ["", "| Verifier | Mutation | Caught / total | Rate |", "|---|---|---|---|"]
        for v, r in rates.items():
            for kind, m in r["by_mutation"].items():
                lines.append(f"| {v} | `{kind}` | {m['caught']} / {m['total']} | {_pct(m['rate'])} |")

    for key, rows in per_key.items():
        lines += ["", f"## {key}", ""]
        case = vc.case_requests(vc.load_case(key))
        expected_path = ROOT / "fixtures" / "expected" / key / "expected.yaml"
        expected = {e["source_path"]: e for e in vc.load_expected(key)["requests"]} if expected_path.is_file() else {}
        order = sorted(rows, key=lambda sp: PRIORITIES.index(rows[sp]["priority"]))  # stable: case order within a priority
        for sp in order:
            row = rows[sp]
            req = case[sp]
            lines.append(f"- **{row['priority']}** `{sp}` ({req['widget_type']}: {_short(req['widget_title'] or '')})")
            signals = ", ".join(
                f"{v} {s}{' (untrusted)' if v in row['untrusted'] else ''}" for v, s in row["signals"].items()
            )
            lines.append(f"  - signals: {signals or 'none'}")
            for v, rs in row["reasons"].items():
                lines.append(f"  - {v}: " + "; ".join(_short(r) for r in rs))
            queries = "; ".join(f"{q['name'] or 'q'}: {_code(q['query'] or '')}" for q in req["queries"]) or "none (not a metrics request)"
            formulas = [f for f in req["formulas"] if f]
            lines.append(f"  - Datadog: {queries}" + (f"; formulas: {', '.join(_code(f) for f in formulas)}" if formulas else ""))
            if sp in expected:
                cand = vc.candidate(expected[sp])
                if cand["outcome"] == "translated":
                    lines.append("  - candidate: " + "; ".join(_code(q) for q in cand["promql"]))
                else:
                    lines.append(f"  - candidate: blocked {', '.join(cand['errors'])}")
            else:
                lines.append("  - candidate: no expected.yaml")
    return "\n".join(lines) + "\n"


def _anchor(key: str) -> str:
    """GitHub heading anchor of `## <key>`."""
    return "".join(ch for ch in key.lower().replace(" ", "-") if ch.isalnum() or ch in "-_")


def write(keys: list[str], out: Path | None = None, rates: dict[str, dict[str, Any]] | None = None) -> Path:
    out = out or TRIAGE_MD
    out.write_text(render(keys, rates or {}))
    return out


def write_verified(rates: dict[str, dict[str, Any]], out: Path | None = None) -> Path:
    """Green requests of every dashboard; tests run these like human-reviewed ones."""
    green = {
        key: [sp for sp, row in priorities(key, rates=rates).items() if row["priority"] == "green"]
        for key in vc.all_keys()
    }
    out = out or VERIFIED_JSON
    out.write_text(json.dumps({k: v for k, v in green.items() if v}, indent=2, sort_keys=True) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("keys", nargs="*", metavar="SVC/ID")
    p.add_argument("--all", action="store_true", help="every case in fixtures/oracle/dashboards.json (default)")
    p.add_argument("--out", type=Path, default=None, help=f"output file (default: {TRIAGE_MD.relative_to(ROOT)})")
    p.add_argument("--catch-rates", action="store_true", help="test the verifiers on the known-bad mutations; distrust weak ones")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    if args.all and args.keys:
        p.error("give case keys or --all (not both)")
    keys = args.keys or vc.all_keys()
    rates = catch_rates(vc.all_keys()) if args.catch_rates else {}
    out = write(keys, args.out, rates)
    if args.out is None:
        write_verified(rates)
    totals = dict.fromkeys(PRIORITIES, 0)
    for key in keys:
        for row in priorities(key, rates=rates).values():
            totals[row["priority"]] += 1
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"{shown}: {len(keys)} dashboard(s), " + ", ".join(f"{n} {p}" for p, n in totals.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
