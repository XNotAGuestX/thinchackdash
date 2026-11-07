// Report, manifest, summary and exit codes. Source-agnostic.
import type { Diagnostic, ItemResult, Outcome, ValidationLevels } from "./target.js";
import { assignSlugs } from "./write.js";

export type Counts = Record<Outcome, number>;

export interface Summary {
  dashboards: Counts;
  widgets: Counts;
  monitors: Counts;
  skippedFiles: number;
}

export interface Report {
  reportVersion: 1;
  provider: string;
  compilerVersion: string;
  format: "json" | "yaml";
  mapping: { sourceId: string; sha256: string };
  summary: Summary;
  items: ItemResult[];
  diagnostics: Diagnostic[];
  validation: ValidationLevels;
}

export interface ManifestEntry {
  path: string;
  kind: "dashboard" | "prometheus-rule" | "check-rule" | "source";
  sourceId: string;
  sha256: string;
  outcome: Outcome | null; // null for source copies
  eligible: boolean; // true only when outcome === "translated"
}

export interface Manifest {
  manifestVersion: 1;
  provider: string;
  compilerVersion: string;
  format: "json" | "yaml";
  entries: ManifestEntry[];
}

function zero(): Counts {
  return { translated: 0, partial: 0, blocked: 0 };
}

export function summarize(items: ItemResult[], skippedFiles: number): Summary {
  const s: Summary = { dashboards: zero(), widgets: zero(), monitors: zero(), skippedFiles };
  for (const i of items) {
    const bucket = i.kind === "dashboard" ? s.dashboards : i.kind === "widget" ? s.widgets : s.monitors;
    bucket[i.outcome]++;
  }
  return s;
}

export function exitCodeFor(items: ItemResult[]): 0 | 2 {
  return items.some((i) => i.outcome !== "translated") ? 2 : 0;
}

function codeUnitCompare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Does `path` lie at or below `itemPath` in the source JSON? */
function within(path: string, itemPath: string): boolean {
  if (itemPath === "") return true;
  return path === itemPath || path.startsWith(itemPath + ".") || path.startsWith(itemPath + "[");
}

/**
 * Order diagnostics by item order, then source path, then emission order
 * (00-context §8). `fileOrder` ranks diagnostics of files that produced no item.
 */
export function orderDiagnostics(diagnostics: Diagnostic[], items: ItemResult[], fileOrder: string[]): Diagnostic[] {
  const fileRank = new Map(fileOrder.map((id, i) => [id, i]));
  const keyed = diagnostics.map((d, emission) => {
    let itemRank = -1;
    let best = -1;
    items.forEach((it, idx) => {
      if (it.sourceId === d.sourceId && within(d.sourcePath, it.sourcePath) && it.sourcePath.length > best) {
        best = it.sourcePath.length;
        itemRank = idx;
      }
    });
    return { d, file: fileRank.get(d.sourceId) ?? fileOrder.length, itemRank, emission };
  });
  keyed.sort(
    (a, b) =>
      a.file - b.file ||
      a.itemRank - b.itemRank ||
      codeUnitCompare(a.d.sourcePath, b.d.sourcePath) ||
      a.emission - b.emission,
  );
  return keyed.map((k) => k.d);
}

function countsLine(label: string, c: Counts): string {
  return `${label} ${c.translated} translated, ${c.partial} partial, ${c.blocked} blocked`;
}

export function formatSummary(report: Pick<Report, "items" | "summary">): string {
  const lines: string[] = [];
  const dashboards = report.items.filter((i) => i.kind === "dashboard");
  const monitors = report.items.filter((i) => i.kind === "monitor");
  const dashSlugs = assignSlugs(dashboards.map((d) => d.title));
  const monSlugs = assignSlugs(monitors.map((m) => m.title));

  dashboards.forEach((d, i) => {
    const widgets = report.items.filter((w) => w.kind === "widget" && w.sourceId === d.sourceId);
    const translated = widgets.filter((w) => w.outcome === "translated").length;
    lines.push(`${d.outcome.toUpperCase().padEnd(10)}${dashSlugs[i]} ${translated}/${widgets.length} widgets`);
  });
  monitors.forEach((m, i) => {
    lines.push(`${m.outcome.toUpperCase().padEnd(10)}${monSlugs[i]}`);
  });

  const s = report.summary;
  lines.push(
    [countsLine("dashboards", s.dashboards), countsLine("widgets", s.widgets), countsLine("monitors", s.monitors), `skipped files ${s.skippedFiles}`].join("; "),
  );
  return lines.join("\n");
}
