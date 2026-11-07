import { describe, expect, it } from "vitest";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { runConvert, type ConvertOptions } from "../src/convert.js";

const POSTGRES = "fixtures/datadog/integrations-core/postgres";

function setup(): { dir: string; mapping: string } {
  const dir = mkdtempSync(join(tmpdir(), "conv-"));
  const mapping = join(dir, "empty-mapping.json");
  writeFileSync(mapping, '{"version":1,"source":"datadog","metrics":{}}');
  return { dir, mapping };
}

function opts(mapping: string, out: string, extra: Partial<ConvertOptions> = {}): ConvertOptions {
  return { source: "datadog", inputs: [POSTGRES], mapping, out, format: "json", project: "datadog-migration", ...extra };
}

function listFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const name of readdirSync(d)) {
      const p = join(d, name);
      if (statSync(p).isDirectory()) walk(p);
      else out.push(relative(root, p));
    }
  };
  walk(root);
  return out.sort();
}

describe("runConvert", () => {
  it("convert writes report, manifest and source copies", () => {
    const { dir, mapping } = setup();
    const out = join(dir, "out");
    const result = runConvert(opts(mapping, out));
    expect(result.exitCode).toBe(2);
    expect(result.report.summary.dashboards.blocked).toBe(2);
    expect(result.report.summary.widgets.blocked).toBe(19);
    expect(result.report.summary.monitors.blocked).toBe(2);
    expect(result.report.validation).toEqual({ syntax: "not_run", schema: "not_run", api: "not_run", semantics: "not_run" });
    expect(result.report.mapping.sourceId).toBe("empty-mapping.json");

    const copy = join(out, "source/postgres/monitors/replication_delay.json");
    expect(existsSync(copy)).toBe(true);
    expect(readFileSync(copy)).toEqual(readFileSync(join(POSTGRES, "monitors/replication_delay.json")));

    const report = JSON.parse(readFileSync(join(out, "report.json"), "utf8"));
    expect(report.reportVersion).toBe(1);
    expect(report.provider).toBe("datadog");
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf8"));
    expect(manifest.entries.map((e: { path: string }) => e.path)).toEqual([
      "source/postgres/dashboards/postgresql_dashboard.json",
      "source/postgres/dashboards/postgresql_screenboard_dashboard.json",
      "source/postgres/monitors/percent_usage_connections.json",
      "source/postgres/monitors/replication_delay.json",
    ]);
    expect(manifest.entries.every((e: { outcome: unknown; eligible: boolean }) => e.outcome === null && e.eligible === false)).toBe(true);
    // No absolute paths leak into artifacts.
    expect(readFileSync(join(out, "report.json"), "utf8")).not.toContain(dir);
  });

  it("orders diagnostics by item order, not by string order of paths", () => {
    const { dir, mapping } = setup();
    const result = runConvert(opts(mapping, join(dir, "out")));
    const metricsPaths = result.report.diagnostics
      .filter((d) => d.sourceId === "postgres/dashboards/postgresql_screenboard_dashboard.json")
      .map((d) => d.sourcePath);
    expect(metricsPaths).toEqual(Array.from({ length: 10 }, (_, i) => `widgets[${i}]`));
  });

  it("counts skipped files and keeps their warning", () => {
    const { dir, mapping } = setup();
    const input = join(dir, "in");
    mkdirSync(input);
    writeFileSync(join(input, "hello.json"), '{"hello":1}');
    const result = runConvert(opts(mapping, join(dir, "out"), { inputs: [input] }));
    expect(result.report.summary.skippedFiles).toBe(1);
    expect(result.report.diagnostics[0]!.code).toBe("UNSUPPORTED_ASSET_KIND");
    expect(result.exitCode).toBe(0);
  });

  it("convert refuses a non-empty out dir", () => {
    const { dir, mapping } = setup();
    const out = join(dir, "out");
    mkdirSync(out);
    writeFileSync(join(out, "stale.json"), "{}");
    expect(() => runConvert(opts(mapping, out))).toThrow(/output directory is not empty/);
  });

  it("rejects a mapping file that is not JSON", () => {
    const { dir } = setup();
    const bad = join(dir, "bad.json");
    writeFileSync(bad, "{");
    expect(() => runConvert(opts(bad, join(dir, "out")))).toThrow(/invalid mapping/);
  });

  it("convert is deterministic", () => {
    const { dir, mapping } = setup();
    const a = join(dir, "a");
    const b = join(dir, "b");
    runConvert(opts(mapping, a));
    runConvert(opts(mapping, b));
    const filesA = listFiles(a);
    expect(filesA).toEqual(listFiles(b));
    for (const f of filesA) expect(readFileSync(join(a, f))).toEqual(readFileSync(join(b, f)));
  });
});
