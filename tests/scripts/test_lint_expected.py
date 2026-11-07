"""Offline tests for scripts/lint_expected.py (L0 deterministic lint of agent0 expectations, issue #87)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lint_expected as le
import verify_common as vc

KEY = "postgres/postgresql_dashboard"
CASE: dict[str, Any] = {"template_variables": []}
COUNTER = 'rate({otel_metric_name="postgresql.tup_fetched", otel_metric_type="sum"}[$__rate_interval])'
COUNTER2 = 'rate({otel_metric_name="postgresql.tup_returned", otel_metric_type="sum"}[$__rate_interval])'
UPDOWN = '{otel_metric_name="postgresql.db_size", otel_metric_type="sum"}'
BACKENDS = '{otel_metric_name="postgresql.backends", otel_metric_type="sum"}'  # grain-pinned (plan 05 Task 4)
GAUGE = '{otel_metric_name="system.cpu.load_average.1m", otel_metric_type="gauge"}'


def request(*queries: str, formulas: list[str] | None = None, names: bool = True) -> dict[str, Any]:
    return {
        "source_path": "widgets[0].definition.requests[0]",
        "queries": [
            {"name": f"query{i + 1}" if names else None, "data_source": "metrics", "query": q} for i, q in enumerate(queries)
        ],
        "formulas": formulas if formulas is not None else (["query1"] if names else []),
    }


def translated(*promql: str) -> dict[str, Any]:
    return {"outcome": "translated", "promql": list(promql), "errors": [], "warnings": []}


def lint(req: dict[str, Any], exp: dict[str, Any], answer: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    findings = le.lint_entry(req, exp, CASE, answer)
    return le.verdict_of(findings), [f.reason for f in findings]


def checks(req: dict[str, Any], exp: dict[str, Any], answer: dict[str, Any] | None = None) -> set[tuple[str, str]]:
    return {(f.check, f.severity) for f in le.lint_entry(req, exp, CASE, answer)}


# --------------------------------------------------------------------------- translated: passing shapes


def test_simple_counter_passes() -> None:
    assert lint(request("avg:postgresql.rows_fetched{*}"), translated(f"avg({COUNTER})")) == ("pass", [])


def test_missing_aggregator_defaults_to_avg() -> None:
    """A query without a space aggregator uses Datadog's default, avg."""
    req = request("postgresql.rows_fetched{*}")
    assert lint(req, translated(f"avg({COUNTER})")) == ("pass", [])


def test_unnamed_query_without_formula() -> None:
    assert lint(request("sum:nginx.net.reading{*}", names=False), translated(f"sum({UPDOWN})"))[0] == "pass"


def test_arithmetic_formula_maps_each_query() -> None:
    req = request("max:a{*}", "sum:b{*}", formulas=["query2 - query1"])
    assert lint(req, translated(f"sum({COUNTER2}) - max({COUNTER})"))[0] == "pass"
    verdict, reasons = lint(req, translated(f"max({COUNTER2}) - sum({COUNTER})"))
    assert verdict == "block"
    assert any("query2: Datadog aggregator sum, PromQL max" in r for r in reasons)
    assert any("query1: Datadog aggregator max, PromQL sum" in r for r in reasons)


def test_ratio_formula_with_literal_from_source_passes() -> None:
    req = request("sum:a{*}", "sum:b{*}", formulas=["query1 / (query1 + query2) * 100"])
    promql = f"sum({COUNTER}) / (sum({COUNTER}) + sum({COUNTER2})) * 100"
    assert lint(req, translated(promql)) == ("pass", [])


# --------------------------------------------------------------------------- one check each


def test_parse_failure_blocks() -> None:
    assert ("parse", "block") in checks(request("avg:a{*}"), translated(f"avg({COUNTER}"))


def test_count_mismatch_blocks() -> None:
    req = request("avg:a{*}")
    assert ("count", "block") in checks(req, translated(f"avg({COUNTER})", f"avg({COUNTER2})"))
    assert ("count", "block") in checks(req, translated())


def test_missing_formula_operator_blocks() -> None:
    req = request("sum:a{*}", "sum:b{*}", formulas=["query1 - query2"])
    verdict, reasons = lint(req, translated(f"sum({COUNTER}) + sum({COUNTER2})"))
    assert verdict == "block" and any("operator(s) -" in r for r in reasons)


def test_bare_selector_is_not_aggregated() -> None:
    assert ("aggregation", "block") in checks(request("avg:a{*}"), translated(COUNTER))


def test_nested_aggregation_is_a_nit() -> None:
    exp = translated(f"max(avg by (host_name) ({COUNTER}))")
    assert lint(request("max:a{*}"), exp)[0] == "nit"


def test_missing_dedup_grain_is_a_nit() -> None:
    """Plan 05 Task 4: postgresql.backends needs a dedup grain on service_instance_id."""
    verdict, reasons = lint(request("avg:postgresql.connections{*} by {db}"), translated(f"avg by (postgresql_database_name) ({BACKENDS})"))
    assert verdict == "nit" and any("grain" in r for r in reasons)


def test_dedup_grain_present_passes_grain_check() -> None:
    exp = translated(f"avg by (postgresql_database_name) (max by (postgresql_database_name, service_instance_id) ({BACKENDS}))")
    assert ("grain", "nit") not in checks(request("avg:postgresql.connections{*} by {db}"), exp)


def test_derived_metric_is_a_nit() -> None:
    exp = translated(f"sum({COUNTER}) - sum({COUNTER2})")
    verdict, reasons = lint(request("sum:nginx.net.conn_dropped_per_s{*}"), exp)
    assert verdict == "nit" and any("derived from 2 aggregations" in r for r in reasons)


def test_grouping_count_must_match() -> None:
    req = request("avg:postgresql.connections{*} by {db}")
    assert lint(req, translated(f"avg by (postgresql_database_name) ({UPDOWN})"))[0] == "pass"
    assert ("grouping", "block") in checks(req, translated(f"avg({UPDOWN})"))
    assert ("grouping", "block") in checks(req, translated(f"avg by (postgresql_database_name, host_name) ({UPDOWN})"))
    assert ("grouping", "block") in checks(req, translated(f"avg without (host_name) ({UPDOWN})"))


def test_grouping_must_follow_answer_labels_map() -> None:
    req = request("avg:postgresql.connections{*} by {db}")
    exp = translated(f"avg by (host_name) ({UPDOWN})")
    assert lint(req, exp)[0] == "pass"  # no answer -> no claim to compare against
    verdict, reasons = lint(req, exp, {"labels": {"db": "postgresql_database_name"}})
    assert verdict == "nit" and any("answer maps tag db -> postgresql_database_name" in r for r in reasons)
    good = translated(f"avg by (postgresql_database_name) ({UPDOWN})")
    assert lint(req, good, {"labels": {"db": "postgresql_database_name"}})[0] == "pass"


def test_rate_only_on_monotonic_sums() -> None:
    req = request("avg:a{*}")
    gauge_rate = 'avg(rate({otel_metric_name="system.cpu.load_average.1m", otel_metric_type="gauge"}[$__rate_interval]))'
    assert ("rate", "block") in checks(req, translated(gauge_rate))
    updown_rate = 'avg(rate({otel_metric_name="postgresql.backends", otel_metric_type="sum"}[$__rate_interval]))'
    assert ("rate", "block") in checks(req, translated(updown_rate))
    assert lint(req, translated(f"avg({UPDOWN})"))[0] == "pass"
    assert lint(req, translated(f"avg({GAUGE})"))[0] == "pass"


def test_monotonic_counter_without_rate_is_a_nit() -> None:
    raw = 'sum({otel_metric_name="postgresql.tup_fetched", otel_metric_type="sum"})'
    assert lint(request("sum:a{*}"), translated(raw))[0] == "nit"


def test_metric_type_must_match_catalog() -> None:
    req = request("avg:a{*}")
    wrong = 'avg({otel_metric_name="system.cpu.load_average.1m", otel_metric_type="sum"})'
    assert ("type", "block") in checks(req, translated(wrong))
    unknown = 'avg({otel_metric_name="no.such.metric", otel_metric_type="gauge"})'
    assert ("type", "block") in checks(req, translated(unknown))


def test_invented_constant_blocks() -> None:
    req = request("avg:system.io.util{*}")
    assert ("literal", "block") in checks(req, translated(f"100 * avg({COUNTER})"))
    assert ("literal", "block") in checks(req, translated(f"avg({COUNTER}) / 1e7"))


def test_numbers_in_names_and_matchers_do_not_count() -> None:
    req = request("avg:system.load.15{*}")
    with_label = 'avg({otel_metric_name="system.cpu.load_average.15m", otel_metric_type="gauge", cpu="15"})'
    assert lint(req, translated(with_label))[0] == "pass"
    assert le.dd_numbers(request("avg:system.load.15{*}")) == []
    assert ("literal", "block") in checks(req, translated(f"15 * {with_label}"))


@pytest.mark.parametrize(
    "promql",
    [
        'avg(rate({otel_metric_name="postgresql.tup_fetched", otel_metric_type="sum"}[5m]))',
        'avg(rate({otel_metric_name="postgresql.tup_fetched", otel_metric_type="sum"}[$__interval]))',
        'avg(rate({otel_metric_name="postgresql.tup_fetched", otel_metric_type="sum"}[$__rate_interval] offset 1h))',
        f"avg(max_over_time(({COUNTER})[1h:]))",
        'avg(rate({otel_metric_name="postgresql.tup_fetched", otel_metric_type="sum"}[4321ms]))',
    ],
)
def test_only_rate_interval_range(promql: str) -> None:
    findings = le.lint_entry(request("avg:a{*}"), translated(promql), CASE, None)
    assert {f.check for f in findings if f.severity == "block"} & {"range", "parse"}


def test_rate_interval_inside_a_string_is_not_substituted() -> None:
    text = 'label_replace(x, "a", "[$__rate_interval]", "b", ".*")'
    assert le.replace_outside_strings(text, le.RATE_INTERVAL, "[1m]") == text


def test_selector_needs_otel_metric_name() -> None:
    req = request("avg:a{*}")
    assert ("selector", "block") in checks(req, translated('avg(postgresql_backends{otel_metric_type="sum"})'))
    assert ("selector", "nit") in checks(req, translated('avg({otel_metric_name="postgresql.backends"})'))


def test_extra_function_is_a_nit() -> None:
    promql = f'avg(label_replace({UPDOWN}, "x", "$1", "y", "(.*)"))'
    assert lint(request("avg:a{*}"), translated(promql))[0] == "nit"


# --------------------------------------------------------------------------- blocked and drift


def test_blocked_needs_an_error_code() -> None:
    req = request("avg:a{*}")
    assert lint(req, {"outcome": "blocked", "errors": ["MISSING_METRIC_MAPPING"]}) == ("pass", [])
    assert lint(req, {"outcome": "blocked", "errors": []})[0] == "block"
    assert lint(req, {"outcome": "blocked", "errors": ["UNSUPPORTED_FORMULA"]})[0] == "nit"
    assert lint(req, {"outcome": "maybe"})[0] == "block"


def test_warning_drift_is_a_nit() -> None:
    exp = translated(f"avg({COUNTER})")
    exp["warnings"] = ["TEMPLATE_VARIABLE_DROPPED"]
    assert ("warnings", "nit") in checks(request("avg:a{*}"), exp)


# --------------------------------------------------------------------------- real dashboards and files


def test_review_md_known_deviations_are_flagged() -> None:
    """fixtures/expected/REVIEW.md section A: `100 *` scaling, nested max(avg by ...), `/ 1e7`."""
    pg = {r["source_path"]: r for r in le.lint_dashboard(KEY)}
    assert pg["widgets[3].definition.requests[0]"]["verdict"] == "block"
    w6 = pg["widgets[6].definition.requests[0]"]
    assert w6["verdict"] == "block" and any("nested aggregation" in r for r in w6["reasons"])
    docker = {r["source_path"]: r for r in le.lint_dashboard("docker_daemon/docker_dashboard")}
    assert docker["widgets[6].definition.requests[0]"]["verdict"] == "block"
    # A1: no dedup grain on the connections metric (plan 05 Task 4).
    w1 = pg["widgets[1].definition.requests[0]"]
    assert w1["verdict"] == "nit" and any("grain" in r for r in w1["reasons"])


def test_results_follow_case_order() -> None:
    case = vc.load_case(KEY)
    assert [r["source_path"] for r in le.lint_dashboard(KEY)] == [r["source_path"] for r in case["requests"]]


def test_verdict_file_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vc, "VERIFY_DIR", tmp_path)
    monkeypatch.setattr(vc, "MUTATIONS_DIR", tmp_path / "mutations")
    assert le.main([KEY]) == 0
    path = tmp_path / KEY / "lint.yaml"
    first = path.read_text()
    assert le.main([KEY]) == 0
    assert path.read_text() == first  # deterministic: no timestamps
    got = vc.read_verdicts(KEY, "lint")
    assert got is not None
    assert list(got.values()) == le.lint_dashboard(KEY)
    assert "verifier: lint" in first and "tool: scripts/lint_expected.py" in first
