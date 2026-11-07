import { describe, expect, it } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function cli(args: string[]) {
  return spawnSync("node_modules/.bin/tsx", ["src/cli.ts", ...args], { encoding: "utf8" });
}

describe("cli", () => {
  it("unknown --source exits 1", () => {
    const r = cli(["convert", "--source", "nope", "--input", "x", "--mapping", "m", "--out", "o"]);
    expect(r.status).toBe(1);
    expect(r.stderr).toMatch(/unknown --source 'nope'/);
  });

  it("converts the postgres folder with exit 2 and a summary", () => {
    const dir = mkdtempSync(join(tmpdir(), "cli-"));
    const mapping = join(dir, "m.json");
    writeFileSync(mapping, '{"version":1,"source":"datadog","metrics":{}}');
    const r = cli(["convert", "--source", "datadog", "--input", "fixtures/datadog/integrations-core/postgres", "--mapping", mapping, "--out", join(dir, "out")]);
    expect(r.status).toBe(2);
    expect(r.stdout).toMatch(/^BLOCKED {3}postgres-metrics 0\/9 widgets$/m);
  });
});
