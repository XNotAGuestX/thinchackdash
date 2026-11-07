import { describe, expect, it } from "vitest";
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { collectInputFiles } from "../src/input.js";

function tree(): string {
  const root = mkdtempSync(join(tmpdir(), "in-"));
  mkdirSync(join(root, "a"));
  writeFileSync(join(root, "b.json"), '{"b":1}');
  writeFileSync(join(root, "a", "z.json"), '{"z":1}');
  writeFileSync(join(root, "a", "readme.md"), "# no");
  return root;
}

describe("collectInputFiles", () => {
  it("collects json files recursively and sorted", () => {
    const root = tree();
    const base = basename(root);
    expect(collectInputFiles([root]).map((f) => f.sourceId)).toEqual([`${base}/a/z.json`, `${base}/b.json`]);
  });

  it("file input uses basename", () => {
    const root = tree();
    const [file] = collectInputFiles([join(root, "b.json")]);
    expect(file!.sourceId).toBe("b.json");
    expect(file!.text).toBe('{"b":1}');
    expect(file!.sha256).toMatch(/^[0-9a-f]{64}$/);
  });

  it("duplicate source ids throw", () => {
    const root = tree();
    expect(() => collectInputFiles([join(root, "b.json"), join(root, "b.json")])).toThrow(/DUPLICATE_SOURCE_ID: b.json/);
  });

  it("missing path throws", () => {
    expect(() => collectInputFiles([join(tmpdir(), "definitely-missing-input.json")])).toThrow(/input not found/);
  });

  it("rejects a non-json file argument", () => {
    const root = tree();
    expect(() => collectInputFiles([join(root, "a", "readme.md")])).toThrow(/must be a \.json file/);
  });

  it("skips symlinks", () => {
    const root = tree();
    symlinkSync(root, join(root, "loop"));
    symlinkSync(join(root, "b.json"), join(root, "link.json"));
    const ids = collectInputFiles([root]).map((f) => f.sourceId);
    expect(ids.some((id) => id.includes("loop") || id.includes("link"))).toBe(false);
    expect(ids).toHaveLength(2);
  });
});
