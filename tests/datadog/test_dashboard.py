import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from migrator.source.asset import CompileOptions, SourceAsset
from migrator.source.datadog.compile_query import TemplateVariable
from migrator.source.datadog.dashboard import (
    DashboardCompilation,
    compile_dashboard,
    read_template_variables,
)
from migrator.source.datadog.mapping import DatadogMapping
from migrator.validation import validate_dashboard
from tests.datadog.helpers import entry, mapping

SYNTH = Path("fixtures/datadog/synthetic/dashboards")
CONN = 'avg ({otel_metric_name="postgresql.backends", otel_metric_type="sum"})'


def asset(body: dict[str, Any], sid: str = "s.json") -> SourceAsset:
    return SourceAsset(sid, "dashboard", str(body.get("title", "")), body, body)


def compile_body(body: dict[str, Any], m: DatadogMapping | None = None) -> DashboardCompilation:
    return compile_dashboard(asset(body), "slug", m or mapping(), CompileOptions("p"))


def compile_file(name: str) -> DashboardCompilation:
    return compile_body(json.loads((SYNTH / name).read_text()))


def errors_by_widget(c: DashboardCompilation) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for d in c.diagnostics:
        if d.severity == "error":
            out[d.source_path.split(".")[0]].append(d.code)
    return dict(out)


def ts(*requests: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"definition": {"type": "timeseries", "title": "T", "requests": list(requests), **extra}}


def new_req(*queries: str, formulas: list[str] | None = None) -> dict[str, Any]:
    req: dict[str, Any] = {
        "queries": [
            {"data_source": "metrics", "name": f"query{i + 1}", "query": q}
            for i, q in enumerate(queries)
        ]
    }
    if formulas is not None:
        req["formulas"] = [{"formula": f} for f in formulas]
    return req


def body(*widgets: dict[str, Any], variables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "title": "D",
        "layout_type": "ordered",
        "template_variables": variables or [],
        "widgets": list(widgets),
    }


def queries_of(c: DashboardCompilation, key: str) -> list[Any]:
    assert c.dashboard is not None
    return [
        q["spec"]["plugin"]["spec"] for q in c.dashboard["spec"]["panels"][key]["spec"]["queries"]
    ]


def test_translates_supported_widgets() -> None:
    c = compile_file("supported.json")
    assert [(i.kind, i.outcome) for i in c.items] == [
        ("dashboard", "translated"),
        ("widget", "translated"),
        ("widget", "translated"),
    ]
    assert c.dashboard is not None
    assert list(c.dashboard["spec"]["panels"]) == ["widget-0", "widget-1"]
    assert queries_of(c, "widget-0") == [{"query": CONN}]
    assert queries_of(c, "widget-1") == [{"query": CONN}]
    assert c.dashboard["spec"]["layouts"] == [
        {
            "kind": "Grid",
            "spec": {
                "items": [
                    {
                        "x": 0,
                        "y": 0,
                        "width": 12,
                        "height": 8,
                        "content": {"$ref": "#/spec/panels/widget-0"},
                    },
                    {
                        "x": 12,
                        "y": 0,
                        "width": 12,
                        "height": 8,
                        "content": {"$ref": "#/spec/panels/widget-1"},
                    },
                ]
            },
        }
    ]
    assert c.dashboard["metadata"] == {"name": "slug", "project": "p"}
    assert validate_dashboard(c.dashboard) == []
    assert [d.code for d in c.diagnostics if d.code == "LAYOUT_APPROXIMATED"] == [
        "LAYOUT_APPROXIMATED"
    ]


def test_blocks_unsupported_widgets_with_exact_codes() -> None:
    c = compile_file("unsupported.json")
    assert errors_by_widget(c) == {
        "widgets[0]": ["UNSUPPORTED_WIDGET"],
        "widgets[1]": ["UNSUPPORTED_DATA_SOURCE"],
        "widgets[3]": ["UNSUPPORTED_WIDGET"],
        "widgets[4]": ["UNSUPPORTED_QUERY_FUNCTION"],
    }
    # Plan 05 Task 5 deliberately changed widget 2 (`query1 * 100`) from blocked to translated.
    assert [i.outcome for i in c.items] == [
        "partial",
        "blocked",
        "blocked",
        "translated",
        "blocked",
        "blocked",
    ]
    assert queries_of(c, "widget-2") == [{"query": f"({CONN}) * 100"}]


def test_blocks_unknown_widget_options() -> None:
    c = compile_file("unsupported.json")
    msg = [d.message for d in c.diagnostics if d.source_path == "widgets[3].definition.time"]
    assert msg == ["Widget option 'time' is not supported."]


def test_blocks_unconvertible_formulas_never_emitting_a_partial_series() -> None:
    c = compile_body(
        body(
            ts(
                new_req(
                    "avg:postgresql.connections{*}",
                    "avg:postgresql.connections{*} by {db}",
                    formulas=["query1", "query2 - query1"],
                )
            )
        )
    )
    assert c.dashboard is None
    [d] = [d for d in c.diagnostics if d.severity == "error"]
    assert (d.code, d.source_path, d.source_text) == (
        "UNSUPPORTED_FORMULA",
        "widgets[0].definition.requests[0].formulas[1].formula",
        "query2 - query1",
    )
    c = compile_body(body(ts(new_req("avg:postgresql.connections{*}", formulas=["top(query1)"]))))
    assert errors_by_widget(c) == {"widgets[0]": ["UNSUPPORTED_FORMULA"]}


def test_abs_formula_translates_and_query_abs_stays_blocked() -> None:
    c = compile_body(body(ts(new_req("avg:postgresql.connections{*}", formulas=["abs(query1)"]))))
    assert c.dashboard is not None and validate_dashboard(c.dashboard) == []
    assert queries_of(c, "widget-0") == [{"query": f"abs({CONN})"}]
    c = compile_body(body(ts(new_req("abs(avg:postgresql.connections{*})"))))
    [d] = [d for d in c.diagnostics if d.severity == "error"]
    assert (d.code, d.message) == ("UNSUPPORTED_QUERY_FUNCTION", "Function 'abs' is not supported.")


def test_formula_uses_only_referenced_queries() -> None:
    c = compile_body(
        body(
            ts(
                new_req(
                    "avg:postgresql.connections{*}",
                    "avg:unmapped.metric{*}",
                    formulas=["query1 / 2"],
                )
            )
        )
    )
    assert queries_of(c, "widget-0") == [{"query": f"({CONN}) / 2"}]


def test_uses_source_layout_when_every_widget_has_one() -> None:
    c = compile_file("layout.json")
    assert c.items[0].outcome == "partial"
    assert c.dashboard is not None
    grids = c.dashboard["spec"]["layouts"]
    assert len(grids) == 1  # the group's only child is blocked, so it has no grid
    assert [(i["x"], i["y"], i["width"], i["height"]) for i in grids[0]["spec"]["items"]] == [
        (0, 0, 8, 6),
        (8, 0, 16, 6),
    ]
    assert c.items[-1].source_path == "widgets[2].definition.widgets[0]"


def test_source_layout_scales_rows_by_three() -> None:
    """Datadog cells are square; one Datadog row becomes three Perses rows (issue #109)."""
    top = ts({"q": "avg:postgresql.connections{*}"})
    top["layout"] = {"x": 0, "y": 0, "width": 12, "height": 1}
    below = ts({"q": "max:postgresql.connections{*}"})
    below["layout"] = {"x": 3, "y": 2, "width": 6, "height": 2}
    c = compile_body({**body(top, below), "layout_type": "ordered"})
    assert _positions(c) == [(0, 0, 24, 3), (6, 6, 12, 6)]


OVERVIEW_GOLDEN = Path("fixtures/expected/postgres-p1/dashboards/postgres-overview.json")


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bool(
        a["x"] < b["x"] + b["width"]
        and b["x"] < a["x"] + a["width"]
        and a["y"] < b["y"] + b["height"]
        and b["y"] < a["y"] + a["height"]
    )


def test_overview_golden_charts_are_readable_and_do_not_overlap() -> None:
    dash = json.loads(OVERVIEW_GOLDEN.read_text())
    panels = dash["spec"]["panels"]
    charts = 0
    for grid in dash["spec"]["layouts"]:
        items = grid["spec"]["items"]
        for i, item in enumerate(items):
            key = item["content"]["$ref"].removeprefix("#/spec/panels/")
            if panels[key]["spec"]["plugin"]["kind"] != "Markdown":
                charts += 1
                assert item["height"] >= 4, key
            assert not any(_overlap(item, other) for other in items[i + 1 :]), key
    assert charts == 5


def _laid_out(x: int, width: int, layout_type: str) -> dict[str, Any]:
    w = ts({"q": "avg:postgresql.connections{*}"})
    w["layout"] = {"x": x, "y": 0, "width": width, "height": 2}
    return {
        **body(w, ts({"q": "avg:postgresql.connections{*}"}) | {"layout": w["layout"]}),
        "layout_type": layout_type,
    }


def _positions(c: DashboardCompilation) -> list[tuple[int, int, int, int]]:
    assert c.dashboard is not None
    return [
        (i["x"], i["y"], i["width"], i["height"])
        for i in c.dashboard["spec"]["layouts"][0]["spec"]["items"]
    ]


def test_free_layout_dashboard_uses_auto_layout() -> None:
    c = compile_body(_laid_out(0, 50, "free"))
    assert c.items[0].outcome == "translated"
    assert _positions(c) == [(0, 0, 12, 8), (12, 0, 12, 8)]


def test_free_layout_dashboard_with_fitting_layouts_uses_auto_layout() -> None:
    c = compile_body(_laid_out(0, 4, "free"))
    assert c.items[0].outcome == "translated"
    assert _positions(c) == [(0, 0, 12, 8), (12, 0, 12, 8)]


def test_ordered_layout_overflowing_twelve_columns_uses_auto_layout() -> None:
    c = compile_body(_laid_out(8, 8, "ordered"))
    assert c.items[0].outcome == "translated"
    assert _positions(c) == [(0, 0, 12, 8), (12, 0, 12, 8)]


def test_reads_template_variable_defaults() -> None:
    assert read_template_variables(
        {"template_variables": [{"name": "db", "prefix": "db", "defaults": ["*"]}]}
    ) == [TemplateVariable("db", "db", "*")]
    assert read_template_variables({"template_variables": [{"name": "s"}]}) == [
        TemplateVariable("s", None, None)
    ]
    assert read_template_variables(
        {"template_variables": [{"name": "s", "prefix": None, "defaults": ["a", "b"]}]}
    ) == [TemplateVariable("s", None, '["a","b"]')]


def test_empty_defaults_list_means_no_default() -> None:
    assert read_template_variables(
        {"template_variables": [{"name": "db", "prefix": "db", "defaults": []}]}
    ) == [TemplateVariable("db", "db", None)]
    # A dashboard fetched from the API may carry `defaults: []`; widgets using the
    # variable must not be blocked with UNSUPPORTED_TEMPLATE_VARIABLE.
    c = compile_body(
        body(
            ts({"q": "avg:postgresql.connections{$db}"}),
            ts({"q": "avg:postgresql.connections{$host}"}),
            variables=[
                {"name": "db", "prefix": "db", "defaults": []},
                {"name": "host", "prefix": "nomapping", "defaults": []},
            ],
        )
    )
    assert [i.outcome for i in c.items] == ["translated", "translated", "translated"]
    assert "UNSUPPORTED_TEMPLATE_VARIABLE" not in [d.code for d in c.diagnostics]
    assert c.dashboard is not None
    [var] = c.dashboard["spec"]["variables"]
    assert var["spec"]["name"] == "db"
    dropped = [d for d in c.diagnostics if d.code == "TEMPLATE_VARIABLE_DROPPED"]
    assert [d.source_path for d in dropped] == ["widgets[1].definition.requests[0].q"]
    assert "(no default)" in dropped[0].message


def test_maps_prefixed_variables_to_list_variable() -> None:
    c = compile_body(
        body(
            ts({"q": "avg:postgresql.connections{$db}"}),
            variables=[{"name": "db", "prefix": "db", "default": "*"}],
        )
    )
    assert c.dashboard is not None
    [var] = c.dashboard["spec"]["variables"]
    assert var["kind"] == "ListVariable"
    assert var["spec"]["plugin"]["spec"]["labelName"] == "postgresql_database_name"
    assert 'postgresql_database_name=~"^($db)$"' in queries_of(c, "widget-0")[0]["query"]
    infos = [d for d in c.diagnostics if d.source_path == "template_variables[0]"]
    assert [d.code for d in infos] == ["OPTION_IGNORED"]
    assert "unverified in Dash0" in infos[0].message
    assert "anchored" in infos[0].message and "not escaped" in infos[0].message
    assert validate_dashboard(c.dashboard) == []


def test_variable_promoted_when_another_use_has_no_metric_mapping() -> None:
    c = compile_body(
        body(
            ts({"q": "avg:postgresql.connections{$db}"}),
            ts({"q": "avg:unmapped{$db}"}),
            variables=[{"name": "db", "prefix": "db", "default": "*"}],
        )
    )
    assert c.dashboard is not None
    [var] = c.dashboard["spec"]["variables"]
    assert var["spec"]["plugin"]["spec"]["labelName"] == "postgresql_database_name"
    assert queries_of(c, "widget-0")[0]["query"] == (
        'avg ({otel_metric_name="postgresql.backends", otel_metric_type="sum", '
        'postgresql_database_name=~"^($db)$"})'
    )
    assert errors_by_widget(c) == {"widgets[1]": ["MISSING_METRIC_MAPPING"]}
    assert "TEMPLATE_VARIABLE_DROPPED" not in [d.code for d in c.diagnostics]


@pytest.mark.parametrize(
    "other_labels",
    [
        pytest.param({"host": "host_name"}, id="lacks-prefix-label"),
        pytest.param({"db": "db_name"}, id="different-label"),
    ],
)
def test_variable_not_promoted_when_mapped_uses_disagree(other_labels: dict[str, str]) -> None:
    m = mapping(postgresql__connections=entry(), postgresql__other=entry(labels=other_labels))
    c = compile_body(
        body(
            ts({"q": "avg:postgresql.connections{$db}"}),
            ts({"q": "avg:postgresql.other{$db}"}),
            variables=[{"name": "db", "prefix": "db", "default": "*"}],
        ),
        m,
    )
    assert c.dashboard is not None
    assert c.dashboard["spec"]["variables"] == []
    dropped = [d.source_path for d in c.diagnostics if d.code == "TEMPLATE_VARIABLE_DROPPED"]
    assert dropped == ["widgets[0].definition.requests[0].q", "widgets[1].definition.requests[0].q"]
    assert "=~" not in queries_of(c, "widget-0")[0]["query"]


def test_note_becomes_markdown() -> None:
    c = compile_body(body({"definition": {"type": "note", "content": "# Hi", "font_size": "14"}}))
    assert c.dashboard is not None
    panel = c.dashboard["spec"]["panels"]["widget-0"]["spec"]
    assert panel["plugin"] == {"kind": "Markdown", "spec": {"text": "# Hi"}}
    assert panel["queries"] == []


def test_query_value_maps_aggregator_to_calculation() -> None:
    def qv(agg: str) -> dict[str, Any]:
        req = new_req("avg:postgresql.connections{*}")
        req["queries"][0]["aggregator"] = agg
        req["response_format"] = "scalar"
        return {"definition": {"type": "query_value", "title": "V", "requests": [req]}}

    c = compile_body(body(qv("last"), qv("percentile")))
    assert c.dashboard is not None
    plugin = c.dashboard["spec"]["panels"]["widget-0"]["spec"]["plugin"]
    assert plugin == {"kind": "StatChart", "spec": {"calculation": "last-number"}}
    assert errors_by_widget(c) == {"widgets[1]": ["UNSUPPORTED_WIDGET"]}
    legacy = {
        "definition": {
            "type": "query_value",
            "requests": [{"q": "avg:postgresql.connections{*}", "aggregator": "max"}],
        }
    }
    c = compile_body(body(legacy))
    assert c.dashboard is not None
    assert c.dashboard["spec"]["panels"]["widget-0"]["spec"]["plugin"]["spec"] == {
        "calculation": "max"
    }


def test_group_children_become_a_titled_grid() -> None:
    group = {
        "definition": {
            "type": "group",
            "title": "G",
            "layout_type": "ordered",
            "background_color": "blue",
            "widgets": [
                {"definition": {"type": "toplist", "requests": []}},
                ts({"q": "avg:postgresql.connections{*}"}),
            ],
        }
    }
    c = compile_body(body(ts({"q": "avg:postgresql.connections{*}"}), group))
    assert c.dashboard is not None
    assert list(c.dashboard["spec"]["panels"]) == ["widget-0", "widget-1-1"]
    grids = c.dashboard["spec"]["layouts"]
    assert [g["spec"].get("display") for g in grids] == [None, {"title": "G"}]
    assert grids[1]["spec"]["items"][0]["content"] == {"$ref": "#/spec/panels/widget-1-1"}
    assert [i.source_path for i in c.items] == [
        "",
        "widgets[0]",
        "widgets[1].definition.widgets[0]",
        "widgets[1].definition.widgets[1]",
    ]
    assert validate_dashboard(c.dashboard) == []


def test_group_by_sets_series_name_format() -> None:
    c = compile_body(
        body(
            ts({"q": "avg:postgresql.connections{*} by {db}"}),
            ts({"q": "avg:postgresql.connections{*} by {db,host}"}),
        )
    )
    assert queries_of(c, "widget-0")[0]["seriesNameFormat"] == "{{postgresql_database_name}}"
    assert queries_of(c, "widget-1")[0]["seriesNameFormat"] == (
        "{{postgresql_database_name}} / {{host_name}}"
    )


def test_all_blocked_dashboard_has_no_artifact() -> None:
    c = compile_body(body({"definition": {"type": "toplist", "requests": []}}))
    assert c.dashboard is None and c.items[0].outcome == "blocked"
    c = compile_body(body())
    assert c.dashboard is None and c.items[0].outcome == "blocked"
    assert any(
        d.severity == "error"
        and d.code == "UNSUPPORTED_WIDGET"
        and d.source_path == "widgets"
        and d.message == "Dashboard has no convertible widgets."
        for d in c.diagnostics
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(template_variables=5),
        lambda b: b["widgets"][0]["definition"]["requests"][0].update(queries=5),
        lambda b: b["widgets"][0]["definition"].update(type=["x"]),
        lambda b: b["widgets"][0]["definition"]["requests"][0]["queries"][0].update(
            aggregator=["avg"]
        ),
    ],
)
def test_malformed_shapes_block_instead_of_raising(mutate: Any) -> None:
    widget = {
        "definition": {
            "type": "query_value",
            "title": "Q",
            "requests": [
                {
                    "queries": [
                        {
                            "data_source": "metrics",
                            "name": "query1",
                            "query": "avg:postgresql.connections{*}",
                            "aggregator": "avg",
                        }
                    ]
                }
            ],
        }
    }
    b = body(widget)
    mutate(b)
    c = compile_body(b)
    if b["template_variables"] == 5:
        return
    assert c.items[1].outcome == "blocked"
    assert any(d.code == "UNSUPPORTED_WIDGET" for d in c.diagnostics)


def test_diagnostics_are_ordered_by_item_then_source_path() -> None:
    c = compile_body(
        body(ts({"q": "avg:postgresql.connections{*}"}, style={}), ts({"q": "bad("}, yaxis={}))
    )
    assert len(c.diagnostics) > 2

    def key(d: Any) -> tuple[int, str]:
        path = d.source_path
        for n in (1, 0):
            if path == f"widgets[{n}]" or path.startswith(f"widgets[{n}]."):
                return (n + 1, path)
        return (0, path)

    assert c.diagnostics == sorted(c.diagnostics, key=key)


def test_group_option_diagnostics_sort_at_the_group_position() -> None:
    group = {
        "definition": {
            "type": "group",
            "title": "G",
            "background_color": "blue",
            "widgets": [ts({"q": "avg:postgresql.connections{*}"}, yaxis={})],
        }
    }
    c = compile_body(body(ts({"q": "avg:postgresql.connections{*}"}, yaxis={}), group))
    assert [d.source_path for d in c.diagnostics if d.code == "OPTION_IGNORED"] == [
        "widgets[0].definition.yaxis",
        "widgets[1].definition.background_color",
        "widgets[1].definition.widgets[0].definition.yaxis",
    ]


def test_source_paths_sort_by_numeric_index() -> None:
    requests = [{"q": "avg:postgresql.connections{*}", "style": {}} for _ in range(11)]
    c = compile_body(body(ts(*requests)))
    assert [d.source_path for d in c.diagnostics if d.code == "OPTION_IGNORED"] == [
        f"widgets[0].definition.requests[{k}].style" for k in range(11)
    ]


TOP_MESSAGE = (
    "Function 'top' is not supported: Datadog ranks each series once over its points "
    "(https://docs.datadoghq.com/dashboards/functions/rank/#top); "
    "PromQL topk ranks at every step, and the docs leave the ranked window open."
)
TOP_ACTION = "Remove top() to convert all series unranked, or migrate this widget manually."


@pytest.mark.parametrize(
    "args",
    [
        *[f"10, '{by}', 'desc'" for by in ("max", "mean", "min", "sum", "last", "l2norm", "area")],
        "5, 'mean', 'asc'",
        "20, 'last', 'desc'",  # LIMIT_TO outside the documented 5/10/25/50/100
        "10",
        "",
    ],
)
def test_top_blocks_in_queries_and_formulas_with_its_reason(args: str) -> None:
    """#117: top() ranks over the window, PromQL topk per step; every form blocks."""
    tail = f", {args}" if args else ""
    cases = [
        (
            {"q": f"top(avg:postgresql.connections{{*}} by {{db}}{tail})"},
            "UNSUPPORTED_QUERY_FUNCTION",
            "widgets[0].definition.requests[0].q",
        ),
        (
            new_req("avg:postgresql.connections{*} by {db}", formulas=[f"top(query1{tail})"]),
            "UNSUPPORTED_FORMULA",
            "widgets[0].definition.requests[0].formulas[0].formula",
        ),
        (
            new_req(
                "avg:postgresql.connections{*} by {db}", formulas=[f"top(query1 / 1000{tail})"]
            ),
            "UNSUPPORTED_FORMULA",
            "widgets[0].definition.requests[0].formulas[0].formula",
        ),
    ]
    for req, code, path in cases:
        c = compile_body(body(ts(req)))
        assert c.dashboard is None
        assert [i.outcome for i in c.items if i.kind == "widget"] == ["blocked"]
        [d] = [d for d in c.diagnostics if d.severity == "error"]
        assert (d.code, d.source_path, d.message, d.suggested_action) == (
            code,
            path,
            TOP_MESSAGE,
            TOP_ACTION,
        )


def test_other_wrapping_functions_keep_the_generic_message() -> None:
    c = compile_body(body(ts({"q": "per_second(avg:postgresql.connections{*})"})))
    [d] = [d for d in c.diagnostics if d.severity == "error"]
    assert (d.code, d.message) == (
        "UNSUPPORTED_QUERY_FUNCTION",
        "Function 'per_second' is not supported.",
    )
    c = compile_body(
        body(ts(new_req("avg:postgresql.connections{*}", formulas=["cumsum(query1)"])))
    )
    [d] = [d for d in c.diagnostics if d.severity == "error"]
    assert (d.code, d.message) == (
        "UNSUPPORTED_FORMULA",
        "Formula syntax is not supported at offset 6.",
    )
