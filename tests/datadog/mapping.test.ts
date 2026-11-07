import { describe, expect, it } from "vitest";
import { parseMapping } from "../../src/source/datadog/mapping.js";

function minimal(): any {
  return {
    version: 1,
    source: "datadog",
    metrics: {
      "postgresql.connections": {
        kind: "gauge",
        target: { name: "postgresql.backends", type: "sum" },
        sourceUnit: "connection",
        targetUnit: "1",
        scale: 1,
        labels: { db: "postgresql_database_name", host: "host_name" },
        ref: "https://docs.datadoghq.com/opentelemetry/guide/metrics_mapping/",
        reviewed: null,
      },
    },
  };
}

describe("parseMapping", () => {
  it("accepts a minimal gauge entry", () => {
    const result = parseMapping(minimal());
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.mapping.metrics["postgresql.connections"]!.target.name).toBe("postgresql.backends");
    }
  });

  it("accepts a reviewed entry", () => {
    const m = minimal();
    m.metrics["postgresql.connections"].reviewed = { by: "someone", date: "2026-11-07", evidence: "metrics_mapping row" };
    expect(parseMapping(m).ok).toBe(true);
  });

  it("rejects dotted label names", () => {
    const m = minimal();
    m.metrics["postgresql.connections"].labels = { db: "db.namespace" };
    const result = parseMapping(m);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join("\n")).toMatch(/metrics\.postgresql\.connections\.labels\.db/);
  });

  it("rejects unknown kind", () => {
    const m = minimal();
    m.metrics["postgresql.connections"].kind = "histogram";
    expect(parseMapping(m).ok).toBe(false);
  });

  it("rejects wrong source", () => {
    const m = minimal();
    m.source = "grafana";
    expect(parseMapping(m).ok).toBe(false);
  });

  it("rejects unknown keys", () => {
    const m = minimal();
    m.metrics["postgresql.connections"].foo = 1;
    expect(parseMapping(m).ok).toBe(false);
    const top = minimal();
    top.foo = 1;
    expect(parseMapping(top).ok).toBe(false);
  });

  it("rejects a malformed review date", () => {
    const m = minimal();
    m.metrics["postgresql.connections"].reviewed = { by: "x", date: "07/11/2026", evidence: "y" };
    expect(parseMapping(m).ok).toBe(false);
  });
});
