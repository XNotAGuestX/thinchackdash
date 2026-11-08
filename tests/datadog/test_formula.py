from migrator.source.datadog.formula import (
    Abs,
    Bin,
    FormulaOk,
    Num,
    Ref,
    RenderedQuery,
    RenderError,
    RenderOk,
    parse_formula,
    render_formula,
)


def parse(text: str) -> FormulaOk:
    r = parse_formula(text)
    assert isinstance(r, FormulaOk), r
    return r


def test_parses_precedence() -> None:
    assert parse("query1 / (query1 + query2) * 100").formula == Bin(
        "*", Bin("/", Ref("query1"), Bin("+", Ref("query1"), Ref("query2"))), Num("100")
    )


def test_renders_subtraction() -> None:
    compiled = {"query1": RenderedQuery("A", []), "query2": RenderedQuery("B", [])}
    assert render_formula(parse("query2 - query1").formula, compiled) == RenderOk("(B) - (A)", [])


def test_renders_minimal_parentheses_that_keep_the_tree() -> None:
    compiled = {"a": RenderedQuery("A", ["l"]), "b": RenderedQuery("B", ["l"])}
    r = render_formula(parse("a / (a + b) * 100").formula, compiled)
    assert r == RenderOk("(A) / ((A) + (B)) * 100", ["l"])
    r = render_formula(parse("a - (b - 1)").formula, compiled)
    assert isinstance(r, RenderOk) and r.promql == "(A) - ((B) - 1)"


def test_blocks_formulas_with_mismatched_grouping() -> None:
    compiled = {"query1": RenderedQuery("A", ["a"]), "query2": RenderedQuery("B", [])}
    assert render_formula(parse("query1 + query2").formula, compiled) == RenderError(
        "grouping mismatch"
    )


def test_rejects_functions_and_junk_in_formulas() -> None:
    for text in ["top(query1, 5, 'mean', 'desc')", "query1 +", "-query1", "1.", "query1 ** 2", ""]:
        assert not parse_formula(text).ok, text


def test_abs_renders_promql_abs_exactly() -> None:
    # Datadog abs(): "Graph the absolute value of the metric."
    # (docs.datadoghq.com/dashboards/functions/arithmetic/#absolute); PromQL abs() is pointwise.
    compiled = {"query1": RenderedQuery("A", ["l"]), "query2": RenderedQuery("B", ["l"])}
    assert parse("abs(query1)").formula == Abs(Ref("query1"))
    assert render_formula(parse("abs(query1)").formula, compiled) == RenderOk("abs(A)", ["l"])
    r = render_formula(parse("abs(query1 - query2) * 2").formula, compiled)
    assert r == RenderOk("abs((A) - (B)) * 2", ["l"])
    r = render_formula(parse("abs(query1) / abs(query2)").formula, compiled)
    assert r == RenderOk("abs(A) / abs(B)", ["l"])


def test_abs_blocks_unsupported_argument_forms() -> None:
    for text in ["abs()", "abs(query1, 2)", "abs(5)", "abs (query1)", "abs(query1", "ABS(query1)"]:
        assert not parse_formula(text).ok, text
