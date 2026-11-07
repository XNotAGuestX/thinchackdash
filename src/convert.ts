// runConvert(): input → provider → writers → report. Offline; no network I/O.
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { collectInputFiles, InputError } from "./input.js";
import { exitCodeFor, orderDiagnostics, summarize, type Manifest, type ManifestEntry, type Report } from "./report.js";
import { getProvider } from "./source/provider.js";
import type { ItemResult, Outcome } from "./target.js";
import { COMPILER_VERSION } from "./version.js";
import { sha256, stableStringify, toYaml } from "./write.js";

export interface ConvertOptions {
  source: string;
  inputs: string[];
  mapping: string;
  out: string;
  format: "json" | "yaml";
  project: string;
}

export interface ConvertResult {
  report: Report;
  manifest: Manifest;
  exitCode: 0 | 1 | 2;
}

function ensureEmptyOutDir(out: string): void {
  if (!existsSync(out)) return;
  if (!statSync(out).isDirectory()) throw new InputError(`output path is not a directory: ${out}`);
  if (readdirSync(out).length > 0) throw new InputError(`output directory is not empty: ${out}`);
}

function readMapping(path: string): { text: string; value: unknown } {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    throw new InputError(`mapping not found: ${path}`);
  }
  try {
    return { text, value: JSON.parse(text) };
  } catch (err) {
    throw new InputError(`invalid mapping: ${basename(path)} is not valid JSON: ${(err as Error).message}`);
  }
}

function outcomeOf(items: ItemResult[], sourceId: string, kind: ItemResult["kind"]): Outcome {
  const item = items.find((i) => i.sourceId === sourceId && i.kind === kind && i.sourcePath === "");
  if (!item) throw new Error(`internal: no ${kind} item for artifact ${sourceId}`);
  return item.outcome;
}

export function runConvert(opts: ConvertOptions): ConvertResult {
  const provider = getProvider(opts.source);
  const files = collectInputFiles(opts.inputs);
  const mapping = readMapping(opts.mapping);
  ensureEmptyOutDir(opts.out);

  const loaded = provider.load(files);
  const compiled = provider.compile(loaded.assets, mapping.value, { project: opts.project });

  const ext = opts.format === "yaml" ? "yaml" : "json";
  const serialize = (v: unknown) => (opts.format === "yaml" ? toYaml(v) : stableStringify(v));
  const entries: ManifestEntry[] = [];

  const write = (rel: string, text: string): string => {
    const full = join(opts.out, rel);
    mkdirSync(dirname(full), { recursive: true });
    writeFileSync(full, text);
    return sha256(text);
  };

  for (const f of files) {
    entries.push({ path: `source/${f.sourceId}`, kind: "source", sourceId: f.sourceId, sha256: write(`source/${f.sourceId}`, f.text), outcome: null, eligible: false });
  }
  for (const d of compiled.dashboards) {
    const path = `dashboards/${d.slug}.${ext}`;
    const outcome = outcomeOf(compiled.items, d.sourceId, "dashboard");
    entries.push({ path, kind: "dashboard", sourceId: d.sourceId, sha256: write(path, serialize(d.dashboard)), outcome, eligible: outcome === "translated" });
  }
  for (const r of compiled.rules) {
    const outcome = outcomeOf(compiled.items, r.sourceId, "monitor");
    const rulePath = `rules/${r.slug}.prometheus.${ext}`;
    entries.push({ path: rulePath, kind: "prometheus-rule", sourceId: r.sourceId, sha256: write(rulePath, serialize(r.prometheusRuleGroup)), outcome, eligible: outcome === "translated" });
    const checkPath = `check-rules/${r.slug}.json`;
    entries.push({ path: checkPath, kind: "check-rule", sourceId: r.sourceId, sha256: write(checkPath, stableStringify(r.checkRule)), outcome, eligible: outcome === "translated" });
  }
  entries.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));

  const skippedFiles = loaded.diagnostics.filter((d) => d.code === "UNSUPPORTED_ASSET_KIND").length;
  const diagnostics = orderDiagnostics(
    [...loaded.diagnostics, ...compiled.diagnostics],
    compiled.items,
    files.map((f) => f.sourceId),
  );

  const report: Report = {
    reportVersion: 1,
    provider: provider.id,
    compilerVersion: COMPILER_VERSION,
    format: opts.format,
    mapping: { sourceId: basename(opts.mapping), sha256: sha256(mapping.text) },
    summary: summarize(compiled.items, skippedFiles),
    items: compiled.items,
    diagnostics,
    validation: { syntax: "not_run", schema: "not_run", api: "not_run", semantics: "not_run" },
  };
  const manifest: Manifest = {
    manifestVersion: 1,
    provider: provider.id,
    compilerVersion: COMPILER_VERSION,
    format: opts.format,
    entries,
  };

  write("report.json", stableStringify(report));
  write("manifest.json", stableStringify(manifest));

  return { report, manifest, exitCode: exitCodeFor(compiled.items) };
}
