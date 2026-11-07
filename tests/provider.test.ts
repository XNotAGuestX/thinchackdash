import { describe, expect, it } from "vitest";
import { collectInputFiles } from "../src/input.js";
import { getProvider } from "../src/source/provider.js";

const EMPTY_MAPPING = { version: 1, source: "datadog", metrics: {} };

describe("provider table", () => {
  it("finds datadog", () => {
    expect(getProvider("datadog").id).toBe("datadog");
  });

  it("rejects unknown ids", () => {
    expect(() => getProvider("grafana")).toThrow(/unknown --source 'grafana'; known: datadog/);
  });

  it("skeleton compile blocks every item of the postgres folder", () => {
    const provider = getProvider("datadog");
    const { assets } = provider.load(collectInputFiles(["fixtures/datadog/integrations-core/postgres"]));
    const result = provider.compile(assets, EMPTY_MAPPING, { project: "p" });
    const kinds = result.items.map((i) => i.kind);
    expect(result.items).toHaveLength(23);
    expect(kinds.filter((k) => k === "dashboard")).toHaveLength(2);
    expect(kinds.filter((k) => k === "widget")).toHaveLength(19);
    expect(kinds.filter((k) => k === "monitor")).toHaveLength(2);
    expect(result.items.every((i) => i.outcome === "blocked")).toBe(true);
    expect(result.items[1]).toMatchObject({ kind: "widget", sourcePath: "widgets[0]", outcome: "blocked" });
    expect(result.dashboards).toEqual([]);
    expect(result.rules).toEqual([]);
    expect(result.diagnostics.filter((d) => d.code === "UNSUPPORTED_WIDGET")).toHaveLength(19);
    expect(result.diagnostics.filter((d) => d.code === "UNSUPPORTED_MONITOR_TYPE")).toHaveLength(2);
  });

  it("an invalid mapping throws an InputError", () => {
    const provider = getProvider("datadog");
    expect(() => provider.compile([], { version: 2 }, { project: "p" })).toThrow(/invalid mapping: /);
  });
});
