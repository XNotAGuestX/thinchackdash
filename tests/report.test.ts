import { describe, expect, it } from "vitest";
import { exitCodeFor, formatSummary, summarize, type Report } from "../src/report.js";
import type { ItemResult } from "../src/target.js";

function item(kind: ItemResult["kind"], outcome: ItemResult["outcome"], extra: Partial<ItemResult> = {}): ItemResult {
  return { sourceId: "s.json", sourcePath: "", kind, title: "T", outcome, ...extra };
}

describe("report", () => {
  it("exit code is 0 with nothing to report", () => {
    expect(exitCodeFor([])).toBe(0);
    expect(exitCodeFor([item("widget", "translated")])).toBe(0);
  });

  it("exit code is 2 for a blocked or partial item", () => {
    expect(exitCodeFor([item("widget", "translated"), item("widget", "blocked")])).toBe(2);
    expect(exitCodeFor([item("dashboard", "partial")])).toBe(2);
  });

  it("summarize counts per kind and outcome", () => {
    const s = summarize([item("dashboard", "partial"), item("widget", "translated"), item("widget", "blocked"), item("monitor", "blocked")], 3);
    expect(s).toEqual({
      dashboards: { translated: 0, partial: 1, blocked: 0 },
      widgets: { translated: 1, partial: 0, blocked: 1 },
      monitors: { translated: 0, partial: 0, blocked: 1 },
      skippedFiles: 3,
    });
  });

  it("formatSummary prints one line per dashboard and monitor, then totals", () => {
    const items = [
      item("dashboard", "partial", { title: "Postgres - Metrics" }),
      item("widget", "translated", { sourcePath: "widgets[0]" }),
      item("widget", "blocked", { sourcePath: "widgets[1]" }),
      item("monitor", "blocked", { sourceId: "m.json", title: "Conn usage" }),
    ];
    const report = { items, summary: summarize(items, 0) } as unknown as Report;
    expect(formatSummary(report).split("\n")).toEqual([
      "PARTIAL   postgres-metrics 1/2 widgets",
      "BLOCKED   conn-usage",
      "dashboards 0 translated, 1 partial, 0 blocked; widgets 1 translated, 0 partial, 1 blocked; monitors 0 translated, 0 partial, 1 blocked; skipped files 0",
    ]);
  });
});
