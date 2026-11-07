import { describe, expect, it } from "vitest";
import { collectInputFiles, type InputFile } from "../../src/input.js";
import { recognizeAssets } from "../../src/source/datadog/assets.js";

function file(sourceId: string, text: string): InputFile {
  return { sourceId, path: sourceId, text, sha256: "" };
}

describe("recognizeAssets", () => {
  it("recognizes the postgres dashboards and monitors", () => {
    const { assets, diagnostics } = recognizeAssets(collectInputFiles(["fixtures/datadog/integrations-core/postgres"]));
    expect(diagnostics).toEqual([]);
    expect(assets.map((a) => a.kind)).toEqual(["dashboard", "dashboard", "monitor", "monitor"]);
    expect(assets[0]!.title).toBe("Postgres - Metrics");
    expect(assets[2]!.body.query).toBe("avg(last_15m):avg:postgresql.percent_usage_connections{*} > 0.9");
    // The title is the monitor's own `name` (definition.name), not the integrations-core wrapper title.
    expect(assets[2]!.title).toMatch(/^\[Postgres\] Number of connections is a/);
  });

  it("recognizes a raw (unwrapped) monitor", () => {
    const raw = { name: "m", type: "query alert", query: "avg(last_5m):avg:x{*} > 1", options: {} };
    const { assets } = recognizeAssets([file("m.json", JSON.stringify(raw))]);
    expect(assets).toHaveLength(1);
    expect(assets[0]!.kind).toBe("monitor");
    expect(assets[0]!.body).toEqual(raw);
    expect(assets[0]!.title).toBe("m");
  });

  it("unknown json is skipped with a warning", () => {
    const { assets, diagnostics } = recognizeAssets([file("x.json", '{"hello":1}')]);
    expect(assets).toEqual([]);
    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({ code: "UNSUPPORTED_ASSET_KIND", severity: "warning", sourceId: "x.json", sourcePath: "" });
  });

  it("invalid json throws", () => {
    expect(() => recognizeAssets([file("bad.json", "{")])).toThrow(/INVALID_JSON: bad.json/);
  });
});
