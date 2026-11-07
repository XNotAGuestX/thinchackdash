// Shared PromQL rendering for Dash0 selectors. No source-specific logic here.

export interface Matcher {
  label: string;
  value: string;
}

const ESCAPES: Record<string, string> = { "\\": "\\\\", '"': '\\"', "\n": "\\n" };

/** Escape a label value for a double-quoted PromQL string, in a single pass. */
export function escapeLabelValue(value: string): string {
  return value.replace(/[\\"\n]/g, (ch) => ESCAPES[ch]!);
}

/**
 * Render `{otel_metric_name="…", otel_metric_type="…"[, label="value"]...}`.
 * Matchers keep the order they are given in.
 */
export function renderSelector(metric: { name: string; type: string }, matchers: Matcher[]): string {
  const parts = [
    `otel_metric_name="${escapeLabelValue(metric.name)}"`,
    `otel_metric_type="${escapeLabelValue(metric.type)}"`,
    ...matchers.map((m) => `${m.label}="${escapeLabelValue(m.value)}"`),
  ];
  return `{${parts.join(", ")}}`;
}
