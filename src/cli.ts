#!/usr/bin/env node
// CLI wiring and exit codes: 0 all translated, 2 partial/blocked items, 1 errors.
import { Command, InvalidArgumentError, Option } from "commander";
import { runConvert } from "./convert.js";
import { InputError } from "./input.js";
import { formatSummary } from "./report.js";
import { COMPILER_VERSION } from "./version.js";

function fail(message: string): never {
  process.stderr.write(`error: ${message}\n`);
  process.exit(1);
}

const program = new Command()
  .name("dash0-dashboard-migrator")
  .description("Deterministic Datadog to Perses converter.")
  .version(COMPILER_VERSION)
  .exitOverride((err) => {
    // Commander's own help/version output exits 0; argument errors exit 1.
    process.exit(err.exitCode === 0 ? 0 : 1);
  });

program
  .command("convert")
  .description("Convert source assets offline into Perses dashboards, rules and a report.")
  .requiredOption("--source <id>", "source provider id (e.g. datadog)")
  .requiredOption("--input <path...>", "input files or folders")
  .requiredOption("--mapping <file>", "reviewed mapping file")
  .requiredOption("--out <dir>", "output directory (must be empty or absent)")
  .addOption(new Option("--format <format>", "dashboard and rule format").choices(["json", "yaml"]).default("json"))
  .option("--project <name>", "Perses project name", (v: string) => {
    if (v.trim() === "") throw new InvalidArgumentError("project must not be empty");
    return v;
  }, "datadog-migration")
  .action((opts: { source: string; input: string[]; mapping: string; out: string; format: "json" | "yaml"; project: string }) => {
    try {
      const result = runConvert({
        source: opts.source,
        inputs: opts.input,
        mapping: opts.mapping,
        out: opts.out,
        format: opts.format,
        project: opts.project,
      });
      process.stdout.write(formatSummary(result.report) + "\n");
      process.exit(result.exitCode);
    } catch (err) {
      if (err instanceof InputError) fail(err.message);
      fail(err instanceof Error ? err.message : String(err));
    }
  });

program.parse();
