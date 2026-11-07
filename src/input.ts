// Collect input files from --input paths: sorted, recursive, symlinks skipped.
import { lstatSync, readdirSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { sha256 } from "./write.js";

export interface InputFile {
  sourceId: string;
  path: string;
  text: string;
  sha256: string;
}

/** Bad arguments or unreadable input. The CLI maps it to exit code 1. */
export class InputError extends Error {
  override name = "InputError";
}

function byCodeUnit(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function walk(dir: string, rel: string, out: { path: string; rel: string }[]): void {
  const names = readdirSync(dir).sort(byCodeUnit);
  for (const name of names) {
    const full = join(dir, name);
    const relPath = rel === "" ? name : `${rel}/${name}`;
    const stat = lstatSync(full);
    if (stat.isSymbolicLink()) continue;
    if (stat.isDirectory()) walk(full, relPath, out);
    else if (stat.isFile() && name.endsWith(".json")) out.push({ path: full, rel: relPath });
  }
}

export function collectInputFiles(paths: string[]): InputFile[] {
  const found: { sourceId: string; path: string }[] = [];
  for (const p of paths) {
    let stat;
    try {
      stat = lstatSync(p);
    } catch {
      throw new InputError(`input not found: ${p}`);
    }
    if (stat.isDirectory()) {
      const entries: { path: string; rel: string }[] = [];
      walk(p, "", entries);
      const base = basename(p.replace(/[\\/]+$/, ""));
      for (const e of entries) found.push({ sourceId: `${base}/${e.rel}`, path: e.path });
    } else if (stat.isFile()) {
      if (!p.endsWith(".json")) throw new InputError(`input file must be a .json file: ${p}`);
      found.push({ sourceId: basename(p), path: p });
    } else {
      throw new InputError(`input is not a regular file or directory: ${p}`);
    }
  }

  const seen = new Set<string>();
  for (const f of found) {
    if (seen.has(f.sourceId)) throw new InputError(`DUPLICATE_SOURCE_ID: ${f.sourceId}`);
    seen.add(f.sourceId);
  }

  return found
    .sort((a, b) => byCodeUnit(a.sourceId, b.sourceId))
    .map((f) => {
      const text = readFileSync(f.path, "utf8");
      return { sourceId: f.sourceId, path: f.path, text, sha256: sha256(text) };
    });
}
