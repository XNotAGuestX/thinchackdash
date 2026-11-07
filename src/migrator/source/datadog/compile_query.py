"""Compile a parsed Datadog MetricQuery to Dash0 PromQL (handoff plans 03, 05, 06).

Check order (the first blocking failure returns ``CompiledError`` with the warnings
collected so far plus that error):

1. any query function            -> UNSUPPORTED_QUERY_FUNCTION
2. metric not in the mapping     -> MISSING_METRIC_MAPPING
3. entry not reviewed            -> UNREVIEWED_MAPPING (warning, continue)
4. filters, in source order      -> UNSUPPORTED_TEMPLATE_VARIABLE / TEMPLATE_VARIABLE_DROPPED
                                    (warning) / MISSING_LABEL_MAPPING
5. group-by tags                 -> MISSING_LABEL_MAPPING

A query without a space aggregator (`system.load.1{*}`) uses Datadog's default, `avg`
(plan 05 Task 1): the Metrics Explorer and the dashboard editor both default to `avg`
(https://docs.datadoghq.com/metrics/explorer/, https://docs.datadoghq.com/dashboards/querying/).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from migrator.promql import (
    Matcher,
    MetricRef,
    format_number,
    render_aggregation,
    render_matchers,
    render_selector,
)
from migrator.source.datadog.mapping import (
    DatadogMapping,
    ExpressionMapping,
    GaugeMapping,
    RateMapping,
)
from migrator.source.datadog.model import MetricQuery, TagFilter
from migrator.target import Diagnostic, DiagnosticCode, Severity

RATE_INTERVAL = "$__rate_interval"
DEFAULT_AGGREGATOR = "avg"  # Datadog's space aggregation without an `agg:` prefix


@dataclass(frozen=True)
class TemplateVariable:
    name: str
    prefix: str | None
    default: str | None


@dataclass(frozen=True)
class QueryContext:
    source_id: str
    source_path: str
    source_text: str
    variables: list[TemplateVariable]
    # Template variables promoted to a Perses ListVariable: name -> target label.
    promoted: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CompiledOk:
    promql: str
    group_labels: list[str]
    diagnostics: list[Diagnostic]
    ok: Literal[True] = True


@dataclass(frozen=True)
class CompiledError:
    diagnostics: list[Diagnostic]
    ok: Literal[False] = False


CompiledQuery = CompiledOk | CompiledError


class _Diags:
    def __init__(self, ctx: QueryContext) -> None:
        self.ctx = ctx
        self.items: list[Diagnostic] = []

    def add(self, code: DiagnosticCode, severity: Severity, message: str, action: str) -> None:
        self.items.append(
            Diagnostic(
                code=code,
                severity=severity,
                source_id=self.ctx.source_id,
                source_path=self.ctx.source_path,
                message=message,
                suggested_action=action,
                source_text=self.ctx.source_text,
            )
        )

    def block(self, code: DiagnosticCode, message: str, action: str) -> CompiledError:
        self.add(code, "error", message, action)
        return CompiledError(diagnostics=list(self.items))


def compile_metric_query(
    q: MetricQuery, mapping: DatadogMapping, ctx: QueryContext
) -> CompiledQuery:
    d = _Diags(ctx)

    if q.functions:
        name = q.functions[0].name
        return d.block(
            "UNSUPPORTED_QUERY_FUNCTION",
            f"Query function '.{name}()' is not supported.",
            "Remove the function from the source query or migrate this widget manually.",
        )
    aggregator = q.aggregator or DEFAULT_AGGREGATOR
    entry = mapping.metrics.get(q.metric)
    if entry is None:
        return d.block(
            "MISSING_METRIC_MAPPING",
            f"No mapping for metric '{q.metric}'.",
            f"Add a reviewed entry for '{q.metric}' to the mapping file.",
        )
    if entry.reviewed is None:
        d.add(
            "UNREVIEWED_MAPPING",
            "warning",
            f"Mapping for '{q.metric}' has not been reviewed by a human.",
            f"Review the '{q.metric}' entry against its ref and set 'reviewed'.",
        )

    matchers: list[Matcher] = []
    for flt in q.filters:
        if isinstance(flt, TagFilter):
            label = entry.labels.get(flt.key)
            if label is None:
                return d.block(
                    "MISSING_LABEL_MAPPING",
                    f"No label mapping for tag '{flt.key}' on metric '{q.metric}'.",
                    f"Add a reviewed label mapping for '{flt.key}' to the '{q.metric}' entry.",
                )
            matchers.append(Matcher(label, flt.value))
            continue
        var = next((v for v in ctx.variables if v.name == flt.name), None)
        if var is None:
            return d.block(
                "UNSUPPORTED_TEMPLATE_VARIABLE",
                f"Template variable '${flt.name}' is not defined on the dashboard.",
                "Define the variable on the source dashboard or remove it from the query.",
            )
        promoted = ctx.promoted.get(var.name)
        if (
            promoted is not None
            and var.prefix is not None
            and entry.labels.get(var.prefix) == promoted
        ):
            # Datadog `$var` filters are exact tag matches. The dashboard substitutes the
            # value at view time, so the closest documented form is the anchored
            # alternation `=~"^($name)$"`: Dash0 does not anchor `=~` and documents exactly
            # this form for whole-value matching
            # (dash0.com/docs/dash0/dashboards/bind-variables-to-queries). Under Prometheus
            # semantics (`=~` is fully anchored) the anchors change nothing.
            # Assumptions and residual gaps (PR #85):
            # - Values are not regex-escaped: a selected `a.b` also matches `aXb`. Perses
            #   documents `${name:regex}`, but its single-backslash output (`(a\.b)`) is an
            #   invalid escape in a double-quoted PromQL string; the double-backslash
            #   `regexliteral` format exists only in Perses' source, is undocumented, and
            #   Dash0 documents no format modifiers. If Dash0 lacked it, `${name:...}` would
            #   stay literal and match nothing, which is worse than a wider match.
            # - What Dash0 substitutes for the "All" default (`$__all`) is undocumented;
            #   Perses expands it to the alternation of all values, which the anchors keep.
            #   Confirm in the Dash0 ListVariable render check (plan 07 Task 1).
            matchers.append(Matcher(promoted, f"^(${var.name})$", "=~"))
        elif var.default is None or var.default == "*":
            default_note = "default '*'" if var.default == "*" else "no default"
            d.add(
                "TEMPLATE_VARIABLE_DROPPED",
                "warning",
                f"Template variable '${var.name}' ({default_note}) dropped; "
                "the converted panel shows all series.",
                "Add a label mapping for the variable's prefix tag to filter by it.",
            )
        else:
            return d.block(
                "UNSUPPORTED_TEMPLATE_VARIABLE",
                f"Template variable '${var.name}' has default '{var.default}'; "
                "dropping it would change the data shown.",
                "Migrate this widget manually or change the variable default to '*'.",
            )

    group_labels: list[str] = []
    for tag in q.group_by:
        label = entry.labels.get(tag)
        if label is None:
            return d.block(
                "MISSING_LABEL_MAPPING",
                f"No label mapping for group-by tag '{tag}' on metric '{q.metric}'.",
                f"Add a reviewed label mapping for '{tag}' to the '{q.metric}' entry.",
            )
        if isinstance(entry, ExpressionMapping) and label not in entry.outputLabels:
            return d.block(
                "MISSING_LABEL_MAPPING",
                f"Label '{label}' is not an output label of the expression for '{q.metric}'.",
                f"Add '{label}' to outputLabels only if the reviewed expression keeps it.",
            )
        group_labels.append(label)

    if isinstance(entry, ExpressionMapping):
        filters = ", " + render_matchers(matchers) if matchers else ""
        body = entry.expression.replace("$filters", filters)
        expr = render_aggregation(aggregator, group_labels, body)
    else:
        expr = _render_metric(entry, aggregator, group_labels, matchers)

    if entry.scale != 1:
        expr = f"({expr}) * {format_number(entry.scale)}"
    return CompiledOk(promql=expr, group_labels=group_labels, diagnostics=list(d.items))


def _render_metric(
    entry: GaugeMapping | RateMapping,
    aggregator: str,
    group_labels: list[str],
    matchers: list[Matcher],
) -> str:
    fixed = [Matcher(label, value) for label, value in sorted(entry.matchers.items())]
    inner = render_selector(MetricRef(entry.target.name, entry.target.type), matchers + fixed)
    if isinstance(entry, RateMapping):
        inner = f"rate({inner}[{RATE_INTERVAL}])"
    if entry.grain is not None:
        grain_by = group_labels + [lbl for lbl in entry.grain.labels if lbl not in group_labels]
        inner = render_aggregation(entry.grain.aggregation, grain_by, inner)
    return render_aggregation(aggregator, group_labels, inner)
