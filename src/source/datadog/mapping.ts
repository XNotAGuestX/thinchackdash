// Datadog mapping file contract. Every metric and label pair used in
// translation comes from a file validated here; nothing is inferred.
import { z } from "zod";

export interface Reviewed {
  by: string;
  date: string; // YYYY-MM-DD
  evidence: string;
}

export interface GaugeMapping {
  kind: "gauge";
  target: { name: string; type: "gauge" | "sum" };
  sourceUnit: string;
  targetUnit: string;
  scale: number;
  labels: Record<string, string>;
  ref: string;
  reviewed: Reviewed | null;
}

// Plan 05 adds RateMapping, plan 06 adds ExpressionMapping.
export type MetricMapping = GaugeMapping;

export interface DatadogMapping {
  version: 1;
  source: "datadog";
  metrics: Record<string, MetricMapping>;
}

const PROMQL_LABEL = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

const reviewedSchema = z.strictObject({
  by: z.string().min(1),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "expected YYYY-MM-DD"),
  evidence: z.string().min(1),
});

const gaugeSchema = z.strictObject({
  kind: z.literal("gauge"),
  target: z.strictObject({
    name: z.string().min(1),
    type: z.enum(["gauge", "sum"]),
  }),
  sourceUnit: z.string().min(1),
  targetUnit: z.string().min(1),
  scale: z.number().finite(),
  labels: z.record(
    z.string().min(1),
    z.string().regex(PROMQL_LABEL, "label must match ^[a-zA-Z_][a-zA-Z0-9_]*$ (Dash0 labels use underscores)"),
  ),
  ref: z.string().min(1),
  reviewed: reviewedSchema.nullable(),
});

const metricSchema = z.discriminatedUnion("kind", [gaugeSchema]);

const mappingSchema = z.strictObject({
  version: z.literal(1),
  source: z.literal("datadog"),
  metrics: z.record(z.string().min(1), metricSchema),
});

export function parseMapping(raw: unknown): { ok: true; mapping: DatadogMapping } | { ok: false; errors: string[] } {
  const result = mappingSchema.safeParse(raw);
  if (result.success) return { ok: true, mapping: result.data };
  const errors = result.error.issues.map((issue) => {
    const path = issue.path.map(String).join(".");
    return `${path === "" ? "(root)" : path}: ${issue.message}`;
  });
  return { ok: false, errors };
}
