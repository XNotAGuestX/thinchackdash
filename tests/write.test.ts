import { describe, expect, it } from "vitest";
import { assignSlugs, sha256, slugify, stableStringify, toYaml } from "../src/write.js";

describe("write", () => {
  it("sorts keys recursively with trailing newline", () => {
    expect(stableStringify({ b: 1, a: { d: [{ z: 1, y: 2 }], c: 2 } }))
      .toBe('{\n  "a": {\n    "c": 2,\n    "d": [\n      {\n        "y": 2,\n        "z": 1\n      }\n    ]\n  },\n  "b": 1\n}\n');
  });
  it("yaml sorts map entries", () => {
    expect(toYaml({ b: 1, a: 2 })).toBe("a: 2\nb: 1\n");
  });
  it("slugifies titles", () => {
    expect(slugify("Postgres - Metrics")).toBe("postgres-metrics");
    expect(slugify("  ##  ")).toBe("dashboard");
    expect(slugify("a".repeat(80))).toHaveLength(64);
  });
  it("disambiguates slug collisions in order", () => {
    expect(assignSlugs(["A b", "a-b", "A B"])).toEqual(["a-b", "a-b-2", "a-b-3"]);
  });
  it("hashes", () => {
    expect(sha256("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});
