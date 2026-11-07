// Deterministic writers: the same value always serializes to the same bytes.
import { createHash } from "node:crypto";
import { stringify as yamlStringify } from "yaml";

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = sortKeys((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  return value;
}

/** JSON with recursively sorted keys, 2-space indent and a trailing newline. */
export function stableStringify(value: unknown): string {
  return JSON.stringify(sortKeys(value), null, 2) + "\n";
}

/** YAML with sorted map entries. */
export function toYaml(value: unknown): string {
  return yamlStringify(value, { sortMapEntries: true });
}

const SLUG_MAX = 64;

export function slugify(title: string): string {
  let slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  slug = slug.slice(0, SLUG_MAX).replace(/-+$/, "");
  return slug === "" ? "dashboard" : slug;
}

/** Slugs in input order; later collisions get `-2`, `-3`, … */
export function assignSlugs(titles: string[]): string[] {
  const used = new Set<string>();
  return titles.map((title) => {
    const base = slugify(title);
    let slug = base;
    for (let n = 2; used.has(slug); n++) slug = `${base}-${n}`;
    used.add(slug);
    return slug;
  });
}

export function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}
