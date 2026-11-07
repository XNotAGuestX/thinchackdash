// Shared output types. Source providers produce these; shared CLI, report,
// validation and destination code consume them. No source-specific logic here.

export type Severity = "error" | "warning" | "info";

export type DiagnosticCode =
  | "UNSUPPORTED_ASSET_KIND"
  | "DUPLICATE_SOURCE_ID"
  | "INVALID_JSON"
  | "UNSUPPORTED_QUERY_SYNTAX"
  | "UNSUPPORTED_QUERY_FUNCTION"
  | "MISSING_AGGREGATOR"
  | "MISSING_METRIC_MAPPING"
  | "MISSING_LABEL_MAPPING"
  | "UNSUPPORTED_UNIT_CONVERSION"
  | "TEMPLATE_VARIABLE_DROPPED"
  | "UNSUPPORTED_TEMPLATE_VARIABLE"
  | "UNREVIEWED_MAPPING"
  | "UNSUPPORTED_WIDGET"
  | "UNSUPPORTED_DATA_SOURCE"
  | "UNSUPPORTED_FORMULA"
  | "LAYOUT_APPROXIMATED"
  | "OPTION_IGNORED"
  | "TARGET_VALIDATION_FAILED"
  | "DEFAULT_AGGREGATOR_APPLIED"
  | "UNSUPPORTED_MONITOR_TYPE"
  | "UNSUPPORTED_MONITOR_OPTION"
  | "MONITOR_OPTION_APPROXIMATED"
  | "THRESHOLD_MISMATCH"
  | "NOTIFICATION_NOT_MIGRATED"
  | "TEXT_TRUNCATED"
  | "IMPORT_FAILED"
  | "IMPORT_RESULT_UNKNOWN";

export interface Diagnostic {
  code: DiagnosticCode;
  severity: Severity;
  sourceId: string;
  sourcePath: string;
  message: string;
  suggestedAction: string;
  sourceText?: string;
}

export type Outcome = "translated" | "partial" | "blocked";
export type ItemKind = "dashboard" | "widget" | "monitor";

export interface ItemResult {
  sourceId: string;
  sourcePath: string;
  kind: ItemKind;
  title: string;
  outcome: Outcome;
}

export interface PersesQuery {
  kind: "TimeSeriesQuery";
  spec: { plugin: { kind: "PrometheusTimeSeriesQuery"; spec: { query: string; seriesNameFormat?: string } } };
}

export interface PersesPanel {
  kind: "Panel";
  spec: {
    display: { name: string; description: string };
    plugin: { kind: string; spec: Record<string, unknown> };
    queries: PersesQuery[];
  };
}

export interface PersesGridItem {
  x: number;
  y: number;
  width: number;
  height: number;
  content: { $ref: string };
}

export interface PersesGrid {
  kind: "Grid";
  spec: { display?: { title: string }; items: PersesGridItem[] };
}

export interface PersesDashboard {
  kind: "Dashboard";
  metadata: { name: string; project: string };
  spec: {
    display: { name: string; description: string };
    duration: string;
    variables: unknown[];
    panels: Record<string, PersesPanel>;
    layouts: PersesGrid[];
  };
}

export interface PrometheusRule {
  alert: string;
  expr: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
}

export interface PrometheusRuleGroup {
  groups: { name: string; interval: string; rules: PrometheusRule[] }[];
}

export interface Dash0CheckRule {
  name: string;
  expression: string;
  thresholds: { degraded?: number; failed: number };
  interval: string;
  for: string;
  enabled: boolean;
  labels: Record<string, string>;
  annotations: { summary: string; description: string };
}

export interface DashboardArtifact {
  slug: string;
  sourceId: string;
  dashboard: PersesDashboard;
}

export interface RuleArtifact {
  slug: string;
  sourceId: string;
  prometheusRuleGroup: PrometheusRuleGroup;
  checkRule: Dash0CheckRule;
}

export interface CompileResult {
  dashboards: DashboardArtifact[];
  rules: RuleArtifact[];
  items: ItemResult[];
  diagnostics: Diagnostic[];
}

export type Level = "passed" | "failed" | "not_run";

export interface ValidationLevels {
  syntax: Level;
  schema: Level;
  api: Level;
  semantics: Level;
}
