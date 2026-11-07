"""Cross-check agent0-derived expectations with Claude Haiku (development time only, issue #87).

Two verifiers, both triage signals (green triage counts as verified; see scripts/triage.py):

  rerun   round-to-round consistency (verifier "rerun"). Compares
          fixtures/oracle/agent0/<key>/answer.yaml with <round2-dir>/<key>/answer.yaml per request,
          after the deterministic rule overlay of scripts/build_expected.py:
            * a request the overlay blocks from the source text alone (no metrics query, any
              Datadog function) is blocked whatever either round answered -> pass, no LLM;
            * missing round-2 item, or different outcome after the overlay (which also blocks
              translations using metrics defined nowhere) -> block, no LLM;
            * both translated and canonical-equal PromQL (tests/support.py) -> pass, no LLM;
            * otherwise Haiku compares the two PromQL lists with the Datadog context:
              equivalent | cosmetic | semantic -> pass | nit | block.
  source  candidate vs Datadog input (verifier "haiku"). For every request in expected.yaml, Haiku
          sees the Datadog request, the anonymous candidate (verify_common.candidate), the OTel
          catalog definitions of the metrics in the candidate PromQL, and the project's hard rules,
          and answers pass | nit | block with reasons and per-aspect checks. Requests the rule
          overlay blocks from the source text are decided without the LLM (pass if the candidate is
          blocked with the rule's error code, else block). --mutations runs on the known-bad
          variants (verify_common.load_mutations) and writes the mutation verdict file.

Every Haiku call is cached under fixtures/oracle/verify/.haiku-cache/<sha256>.json, keyed by
sha256(model, system prompt, user content, schema); the cache is committed, so reruns are free and
give identical verdict files. Output is forced to a JSON schema (structured outputs,
output_config.format) and validated again here; an invalid answer is asked again (--retries).

Auth: ANTHROPIC_API_KEY from the environment or the repository .env (never printed).
Nothing under src/ imports this module and the converter never reads its output (AGENTS.md).

Usage:
  uv run scripts/crosscheck.py rerun  <svc>/<id> [...] | --all [--round2-dir DIR] [--dry-run]
  uv run scripts/crosscheck.py input <svc>/<id> [...] | --all [--mutations] [--dry-run]
  options: --jobs N (parallel requests, default 8), --retries N (schema-invalid answers, default 2)
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tests"))

import build_expected  # noqa: E402
import verify_common as vc  # noqa: E402
from support import canonical_promql  # noqa: E402

MODEL = "claude-haiku-5-5"
EFFORT = "medium"  # Haiku 5.5 default, set explicitly; sampling params (temperature) are rejected on this model
MAX_TOKENS = 16000
AGENT0_DIR = ROOT / "fixtures" / "oracle" / "agent0"
DEFAULT_ROUND2_DIR = ROOT / "fixtures" / "oracle" / "agent0-r2"
CACHE_DIR = vc.VERIFY_DIR / ".haiku-cache"
PROMPT_MD = ROOT / "fixtures" / "oracle" / "PROMPT.md"
DATASET_LABELS = ROOT / "fixtures" / "oracle" / "dash0_default_labels.yaml"

CHECKS = ("quantity", "unit", "aggregation", "grouping", "series_count", "outcome")
CHECK_VALUES = ("ok", "differs", "n/a")
JUDGEMENT_VERDICT = {"equivalent": "pass", "cosmetic": "nit", "semantic": "block"}


class InvalidOutput(Exception):
    """The model's answer does not follow the schema. Asked again up to --retries times."""


# --------------------------------------------------------------------------- prompts


def prompt_section(heading: str, path: Path = PROMPT_MD) -> str:
    """The bullet list that follows the line starting with `heading` in fixtures/oracle/PROMPT.md."""
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(heading):
            out = []
            for item in lines[i + 1 :]:
                if not item.strip():
                    break
                out.append(item)
            if out:
                return "\n".join(out)
    raise SystemExit(f"error: no '{heading}' list in {path}")


VERDICT_DEFINITIONS = """\
Verdicts:
- pass: the candidate computes the same quantity, in the same unit, with the same aggregation, the same
  grouping and the same series as the Datadog request; or the request is correctly blocked.
- nit: the same numbers apart from a unit scale (e.g. ratio vs percent, bytes vs bits), a series grain
  caveat, or a cosmetic difference.
- block: a different quantity, aggregation, grouping or set of series, or the wrong outcome (translated
  although the rules require blocking, or blocked although an exact translation exists)."""


def source_system_prompt() -> str:
    return f"""\
You review one candidate test expectation for a deterministic converter that turns Datadog dashboard
queries into PromQL over OpenTelemetry metrics in Dash0. You get the Datadog request (queries, formulas,
template variables), the candidate (outcome `translated` with one PromQL string per displayed formula, or
`blocked` with error codes), and the OpenTelemetry definitions of the metrics the candidate PromQL uses
("in_dataset" says whether the metric exists in the target dataset; a defined metric that is not in the
dataset is still a valid translation). "dataset_labels" lists label keys observed in the target dataset for
that metric family ("observed") and attribute-derived names known NOT to exist there ("absent"). The dataset
can use newer semantic-convention label names than the catalog "attributes": when they differ, the observed
dataset labels are authoritative for label names in the PromQL.

Decide whether the candidate is what the converter should produce. Judge from what the metrics measure
(definition, unit, instrument type, attributes), never from similar names alone. Check each aspect:
quantity, unit, aggregation, grouping, series_count, outcome; answer ok, differs, or n/a (e.g. for a
blocked candidate). Give short, concrete reasons; nit and block need at least one.

Dash0 PromQL conventions:
{prompt_section("Dash0 PromQL conventions:")}

Hard rules. The converter is deterministic; anything these rules do not allow is blocked:
{prompt_section("Hard rules.")}

Error codes for blocked requests: UNSUPPORTED_DATA_SOURCE (no metrics query), UNSUPPORTED_FORMULA (function
in a formula), UNSUPPORTED_QUERY_FUNCTION (function in a query), MISSING_METRIC_MAPPING (no OTel metric
measures the same quantity).

{VERDICT_DEFINITIONS}
"""


def rerun_system_prompt() -> str:
    return f"""\
You compare two candidate PromQL translations, A and B, of the same Datadog dashboard request. The PromQL
runs in Dash0 over OpenTelemetry metrics. You get the Datadog request and the OpenTelemetry definitions of
every metric either candidate uses.

Answer one judgement:
- equivalent: A and B return the same numbers for the same series (they differ only in formatting,
  matcher or label order, redundant parentheses, or an equivalent way to write the same selection).
- cosmetic: same quantity, aggregation, grouping and series, but one applies a unit scale the other does
  not (e.g. ratio vs percent), or they differ in a documented series grain caveat or presentation only.
- semantic: a different metric or quantity, aggregation, grouping, matchers, rate vs raw value, or number
  of series.
Judge from what the metrics measure, never from similar names alone. Give one short, concrete reason.

Dash0 PromQL conventions:
{prompt_section("Dash0 PromQL conventions:")}
"""


SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(vc.VERDICTS)},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "checks": {
            "type": "object",
            "properties": {c: {"type": "string", "enum": list(CHECK_VALUES)} for c in CHECKS},
            "required": list(CHECKS),
            "additionalProperties": False,
        },
    },
    "required": ["verdict", "reasons", "checks"],
    "additionalProperties": False,
}

RERUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "judgement": {"type": "string", "enum": list(JUDGEMENT_VERDICT)},
        "reason": {"type": "string"},
    },
    "required": ["judgement", "reason"],
    "additionalProperties": False,
}


def _reasons(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        raise InvalidOutput("reasons must be a list of strings")
    return [r.strip() for r in raw if r.strip()]


def validate_source(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"verdict", "reasons", "checks"}:
        raise InvalidOutput(f"expected keys verdict, reasons, checks; got {raw!r:.200}")
    verdict = raw["verdict"]
    if verdict not in vc.VERDICTS:
        raise InvalidOutput(f"verdict must be one of {vc.VERDICTS}, got {verdict!r}")
    reasons = _reasons(raw["reasons"])
    if verdict != "pass" and not reasons:
        raise InvalidOutput(f"{verdict} without a reason")
    checks = raw["checks"]
    if not isinstance(checks, dict) or set(checks) != set(CHECKS) or any(v not in CHECK_VALUES for v in checks.values()):
        raise InvalidOutput(f"checks must map {CHECKS} to {CHECK_VALUES}, got {checks!r:.200}")
    return {"verdict": verdict, "reasons": reasons, "checks": {c: checks[c] for c in CHECKS}}


def validate_rerun(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"judgement", "reason"}:
        raise InvalidOutput(f"expected keys judgement, reason; got {raw!r:.200}")
    if raw["judgement"] not in JUDGEMENT_VERDICT:
        raise InvalidOutput(f"judgement must be one of {list(JUDGEMENT_VERDICT)}, got {raw['judgement']!r}")
    if not isinstance(raw["reason"], str) or not raw["reason"].strip():
        raise InvalidOutput("reason must be a non-empty string")
    return {"judgement": raw["judgement"], "reason": raw["reason"].strip()}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def dump(doc: Any) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)


# --------------------------------------------------------------------------- client + cache


class Judge(Protocol):
    model: str

    async def complete(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        """Ask once; return the parsed JSON answer. Raise InvalidOutput if it is not JSON."""


class AnthropicJudge:
    """Claude via the Anthropic SDK, structured output forced with output_config.format."""

    def __init__(self, api_key: str, model: str = MODEL, effort: str = EFFORT):
        import anthropic  # uv group "oracle"

        self.model = model
        self.effort = effort
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=4)

    async def complete(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": self.effort},  # type: ignore[typeddict-item]
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise InvalidOutput(f"stop_reason {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise InvalidOutput("no text block in the response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidOutput(f"not JSON: {exc}") from exc


@dataclasses.dataclass(frozen=True)
class Call:
    label: str
    system: str
    user: str
    schema: dict[str, Any]
    validate: Callable[[Any], dict[str, Any]]

    def cache_key(self, model: str) -> str:
        return sha256(
            json.dumps(
                {"model": model, "system": self.system, "user": self.user, "schema": self.schema},
                sort_keys=True,
                ensure_ascii=False,
            )
        )


class Asker:
    """Cached, bounded-parallel, schema-validating calls to one Judge."""

    def __init__(self, judge: Judge | None, *, model: str = MODEL, cache_dir: Path | None = None, jobs: int = 8, retries: int = 2):
        self.judge = judge
        self.model = judge.model if judge is not None else model
        self.cache_dir = cache_dir or CACHE_DIR
        self.retries = retries
        self._sem = asyncio.Semaphore(max(1, jobs))
        self.sent = 0
        self.hits = 0

    def cache_path(self, call: Call) -> Path:
        return self.cache_dir / f"{call.cache_key(self.model)}.json"

    def cached(self, call: Call) -> bool:
        return self.cache_path(call).is_file()

    async def ask(self, call: Call) -> tuple[dict[str, Any], str]:
        path = self.cache_path(call)
        key = path.stem
        if path.is_file():
            self.hits += 1
            return call.validate(json.loads(path.read_text())["output"]), key
        if self.judge is None:
            raise RuntimeError(f"{call.label}: not cached and no client (dry run)")
        last: InvalidOutput | None = None
        async with self._sem:
            for _ in range(self.retries + 1):
                self.sent += 1
                try:
                    out = call.validate(await self.judge.complete(call.system, call.user, call.schema))
                    break
                except InvalidOutput as exc:
                    last = exc
            else:
                raise InvalidOutput(f"{call.label}: invalid answer after {self.retries + 1} attempt(s): {last}")
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"model": self.model, "system_sha256": sha256(call.system), "output": out}
        path.write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
        return out, key


# --------------------------------------------------------------------------- plans


@dataclasses.dataclass
class Item:
    """One result row: decided deterministically (`result`) or by `call` + `finish`."""

    source_path: str
    result: dict[str, Any] | None = None
    call: Call | None = None
    finish: Callable[[dict[str, Any], str], dict[str, Any]] | None = None


def metric_definitions(promql: list[str]) -> dict[str, Any]:
    """Catalog definition and dataset presence of every OTel metric selected in `promql`."""
    catalog, present = build_expected.otel_catalog(), build_expected.dataset_metrics()
    names = sorted({m for q in promql for m, _ in build_expected.METRIC_SELECTOR.findall(q)})
    out: dict[str, Any] = {}
    for name in names:
        entry = catalog.get(name)
        if entry is None:
            d: dict[str, Any] = {"defined_by_opentelemetry": False}
        else:
            d = {k: entry[k] for k in ("type", "monotonic", "unit", "attributes")}
            if entry.get("brief"):
                d["brief"] = entry["brief"]
        d["in_dataset"] = name in present
        if name in present:
            d["dataset_types"] = present[name]
            observed = dataset_labels(name)
            if observed is not None:
                d["dataset_labels"] = observed
        out[name] = d
    return out


def dataset_labels(name: str) -> dict[str, list[str]] | None:
    """Label keys observed in the dataset for the metric's prefix (fixtures/oracle/dash0_default_labels.yaml)."""
    if not DATASET_LABELS.is_file():
        return None
    prefixes = yaml.safe_load(DATASET_LABELS.read_text())["prefixes"]
    for prefix in sorted(prefixes, key=len, reverse=True):
        if name.startswith(prefix):
            entry = prefixes[prefix]
            return {"observed": list(entry["labels"]), "absent": list(entry.get("absent") or [])}
    return None


def case_variables(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Template variables by name, as build_expected.py passes them to its overlay."""
    return {v["name"]: v for v in case.get("template_variables") or []}


def rule_blocked(request: dict[str, Any], variables: dict[str, dict[str, Any]]) -> bool:
    """The overlay of build_expected.py blocks this request from its source text alone."""
    return (
        not request["queries"]
        or bool(build_expected.blocked_functions(request))
        or bool(build_expected.rule_errors(request, variables))
    )


def plan_source(key: str, *, mutations: bool = False) -> list[Item]:
    case = vc.load_case(key)
    requests = vc.case_requests(case)
    variables = case_variables(case)
    if mutations:
        entries = [(m["id"], m["source_path"], {"expected": m["expected"]}) for m in vc.load_mutations(key)]
    else:
        entries = [(e["source_path"], e["source_path"], e) for e in vc.load_expected(key)["requests"]]
    system = source_system_prompt()
    items = []
    for row_id, sp, entry in entries:
        req = requests[sp]
        cand = vc.candidate(entry)
        if rule_blocked(req, variables):
            want = build_expected.rule_errors(req, variables)
            ok = cand["outcome"] == "blocked" and cand.get("errors") == want
            reason = [] if ok else [f"the rule overlay blocks this request with {want}; candidate is {cand['outcome']} {cand.get('errors', '')}".strip()]
            items.append(Item(row_id, {"source_path": row_id, "verdict": "pass" if ok else "block", "reasons": reason, "method": "rule"}))
            continue
        user = dump(
            {
                "datadog_request": vc.request_context(case, sp),
                "candidate": cand,
                "otel_metric_definitions": metric_definitions(cand.get("promql", [])),
            }
        )

        def finish(out: dict[str, Any], cache: str, row_id: str = row_id) -> dict[str, Any]:
            return {"source_path": row_id, **out, "method": "haiku", "cache": cache[:16]}

        items.append(Item(row_id, call=Call(f"{key} {row_id}", system, user, SOURCE_SCHEMA, validate_source), finish=finish))
    return items


def load_answers(path: Path) -> dict[str, dict[str, Any]] | None:
    if not path.is_file():
        return None
    return {a["source_path"]: a for a in (yaml.safe_load(path.read_text()) or [])}


def overlaid(request: dict[str, Any], item: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Outcome and PromQL of one answer item after build_expected's overlay (rule-blocked handled before)."""
    if item is None or item.get("outcome") != "translated":
        return "blocked", []
    promql = list(item.get("promql") or [])
    undefined, _, _ = build_expected.metric_availability(promql)
    return ("blocked", []) if undefined else ("translated", promql)


def canonical_equal(a: list[str], b: list[str]) -> bool:
    if len(a) != len(b):
        return False
    try:
        return [canonical_promql(q) for q in a] == [canonical_promql(q) for q in b]
    except Exception:  # unparsable PromQL: let the model decide
        return False


def plan_rerun(key: str, round2_dir: Path, agent0_dir: Path | None = None) -> list[Item] | None:
    """None when round 2 has no answer for this dashboard."""
    agent0_dir = agent0_dir or AGENT0_DIR
    second = load_answers(round2_dir / key / "answer.yaml")
    if second is None:
        return None
    first = load_answers(agent0_dir / key / "answer.yaml") or {}
    case = vc.load_case(key)
    system = rerun_system_prompt()
    items = []
    for req in case["requests"]:
        sp = req["source_path"]

        def row(verdict: str, method: str, reasons: list[str], sp: str = sp, **extra: Any) -> Item:
            return Item(sp, {"source_path": sp, "verdict": verdict, "reasons": reasons, "method": method, **extra})

        if rule_blocked(req, case_variables(case)):
            items.append(row("pass", "rule", []))
            continue
        if sp not in second:
            items.append(row("block", "missing", ["outcome differs between rounds: round 2 has no answer"]))
            continue
        o1, p1 = overlaid(req, first.get(sp))
        o2, p2 = overlaid(req, second[sp])
        if o1 != o2:
            items.append(row("block", "outcome", [f"outcome differs between rounds: {o1} vs {o2}"], round2={"outcome": o2, "promql": p2}))
        elif o1 == "blocked":
            items.append(row("pass", "outcome", []))
        elif canonical_equal(p1, p2):
            items.append(row("pass", "canonical", []))
        else:
            user = dump(
                {
                    "datadog_request": vc.request_context(case, sp),
                    "candidate_a": p1,
                    "candidate_b": p2,
                    "otel_metric_definitions": metric_definitions(p1 + p2),
                }
            )

            def finish(out: dict[str, Any], cache: str, sp: str = sp, p2: list[str] = p2) -> dict[str, Any]:
                verdict = JUDGEMENT_VERDICT[out["judgement"]]
                return {
                    "source_path": sp,
                    "verdict": verdict,
                    "reasons": [out["reason"]],
                    "method": "haiku",
                    "judgement": out["judgement"],
                    "round2": {"outcome": "translated", "promql": p2},
                    "cache": cache[:16],
                }

            items.append(Item(sp, call=Call(f"{key} {sp}", system, user, RERUN_SCHEMA, validate_rerun), finish=finish))
    return items


async def execute(items: list[Item], asker: Asker) -> list[dict[str, Any]]:
    async def one(item: Item) -> dict[str, Any]:
        if item.result is not None:
            return item.result
        assert item.call is not None and item.finish is not None
        out, cache = await asker.ask(item.call)
        return item.finish(out, cache)

    return list(await asyncio.gather(*(one(i) for i in items)))


def meta(verifier: str, model: str) -> dict[str, Any]:
    system = source_system_prompt() if verifier == "haiku" else rerun_system_prompt()
    schema = SOURCE_SCHEMA if verifier == "haiku" else RERUN_SCHEMA
    return {
        "tool": "scripts/crosscheck.py",
        "model": model,
        "effort": EFFORT,
        "max_tokens": MAX_TOKENS,
        "structured_output": "output_config.format json_schema",
        "system_prompt_sha256": sha256(system),
        "schema_sha256": sha256(json.dumps(schema, sort_keys=True)),
        "cache": str(CACHE_DIR.relative_to(ROOT)) if CACHE_DIR.is_relative_to(ROOT) else str(CACHE_DIR),
    }


HEADERS = {
    "haiku": "Claude Haiku cross-check of each candidate against the Datadog request (scripts/crosscheck.py input).",
    "rerun": "Round-to-round consistency of the oracle answers (scripts/crosscheck.py rerun); deterministic first, Haiku only for differing PromQL.",
}


# --------------------------------------------------------------------------- CLI


def read_env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == name and v.strip():
                return v.strip().strip("'\"")
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("mode", choices=["rerun", "input"])
    p.add_argument("keys", nargs="*", metavar="SVC/ID")
    p.add_argument("--all", action="store_true", help="every case in fixtures/oracle/dashboards.json")
    p.add_argument("--mutations", action="store_true", help="input mode: check the known-bad variants")
    p.add_argument("--dry-run", action="store_true", help="list the calls and cache hits; no network")
    p.add_argument("--round2-dir", type=Path, default=DEFAULT_ROUND2_DIR, help="rerun mode (default: %(default)s)")
    p.add_argument("--jobs", "-j", type=int, default=8, help="parallel requests (default: 8)")
    p.add_argument("--retries", type=int, default=2, help="re-asks after a schema-invalid answer (default: 2)")
    args = p.parse_args(argv)
    if bool(args.keys) == args.all:
        p.error("give case keys or --all (not both)")
    if args.mutations and args.mode != "input":
        p.error("--mutations only applies to input mode")
    if args.jobs < 1 or args.retries < 0:
        p.error("--jobs must be >= 1 and --retries >= 0")
    return args


def main(argv: list[str] | None = None, judge: Judge | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    keys = vc.all_keys() if args.all else args.keys
    verifier = "haiku" if args.mode == "input" else "rerun"
    if args.mode == "rerun" and not args.round2_dir.is_dir():
        print(
            f"error: no round-2 answers: {args.round2_dir} does not exist;"
            " run scripts/run_oracle.py --mode answer --out-dir fixtures/oracle/agent0-r2 first",
            file=sys.stderr,
        )
        return 2

    plans: dict[str, list[Item]] = {}
    for key in keys:
        if args.mode == "input":
            if not (ROOT / "fixtures" / "expected" / key / "expected.yaml").is_file():
                print(f"skip {key}: no expected.yaml", file=sys.stderr)
                continue
            items = plan_source(key, mutations=args.mutations)
            if args.mutations and not items:
                print(f"skip {key}: no mutations (scripts/mutate_expected.py)", file=sys.stderr)
                continue
            plans[key] = items
        else:
            rerun_items = plan_rerun(key, args.round2_dir)
            if rerun_items is None:
                print(f"skip {key}: no round-2 answer under {args.round2_dir}", file=sys.stderr)
                continue
            plans[key] = rerun_items
    if not plans:
        if args.mode == "rerun":
            print(
                f"error: no round-2 answers under {args.round2_dir} for the selected cases;"
                " run scripts/run_oracle.py --mode answer --out-dir fixtures/oracle/agent0-r2 first",
                file=sys.stderr,
            )
        else:
            print("error: nothing to check for the selected cases", file=sys.stderr)
        return 2

    if args.dry_run:
        asker = Asker(None, jobs=args.jobs)
        total = hits = decided = 0
        for key, items in plans.items():
            calls = [i for i in items if i.call is not None]
            cached = sum(asker.cached(i.call) for i in calls if i.call is not None)
            decided += len(items) - len(calls)
            total += len(calls)
            hits += cached
            print(f"{key}: {len(items)} rows, {len(items) - len(calls)} decided without LLM, {len(calls)} Haiku calls ({cached} cached)")
            for i in calls:
                assert i.call is not None
                state = "cached" if asker.cached(i.call) else "would send"
                print(f"  {i.source_path}  {i.call.cache_key(asker.model)[:16]}  {state}")
        print(f"dry run ({verifier}{', mutations' if args.mutations else ''}, {MODEL}): {decided} rows decided without LLM, {total} calls, {hits} cache hits, {total - hits} to send")
        return 0

    if judge is None and any(i.call is not None and not Asker(None).cached(i.call) for items in plans.values() for i in items):
        api_key = read_env("ANTHROPIC_API_KEY")
        if not api_key:
            print("error: ANTHROPIC_API_KEY is not set (environment or .env)", file=sys.stderr)
            return 2
        judge = AnthropicJudge(api_key)
    asker = Asker(judge, jobs=args.jobs, retries=args.retries)
    model = asker.model

    async def run_all() -> dict[str, list[dict[str, Any]] | BaseException]:
        outs = await asyncio.gather(*(execute(items, asker) for items in plans.values()), return_exceptions=True)
        return dict(zip(plans, outs))

    failed = 0
    for key, out in asyncio.run(run_all()).items():
        if isinstance(out, BaseException):
            failed += 1
            print(f"failed {key}: {type(out).__name__}: {out}", file=sys.stderr)
            continue
        path = vc.write_verdicts(key, verifier, out, meta(verifier, model), header=HEADERS[verifier], mutations=args.mutations)
        counts = {v: sum(r["verdict"] == v for r in out) for v in vc.VERDICTS}
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: " + ", ".join(f"{n} {v}" for v, n in counts.items()))
    print(f"{asker.sent} request(s) sent, {asker.hits} cache hit(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
