import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { COMPILER_VERSION } from "../src/version.js";

describe("version", () => {
  it("matches package.json", () => {
    const pkg = JSON.parse(readFileSync("package.json", "utf8"));
    expect(COMPILER_VERSION).toBe(pkg.version);
  });
});
