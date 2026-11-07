# Datadog → Dash0 Migrator

**Highest priority, before anything else: deterministic conversion. No LLM shall be involved in translation from a source to Perses, PromQL, or Dash0 check rules—not even as an optional fallback. Reduce MVP scope before compromising this rule.** Codex and Claude may help write the implementation and tests; migration itself uses explicit parsers, mappings, and deterministic rules only.

Hackathon project: a deterministic CLI that migrates Datadog dashboards and metric monitors to Dash0 using Perses, PromQL, and explicit telemetry mappings.

**Current state: plan 01 done.** `convert` runs offline, recognizes Datadog dashboards and monitors, and reports every widget and monitor, but it translates nothing yet: every item is `blocked` until plans 04 (dashboards) and 06 (monitors). Publishing to Dash0 isn't implemented.

## Start here

1. Read [AGENTS.md](AGENTS.md), the shared working rules for humans, Codex, and Claude.
2. Read [the implementation plan overview](docs/handoff/README.md): which handoff holds what, the order of work, and the open decisions. Each numbered file in `docs/handoff/` is a self-contained, test-first plan.
3. Background: [the project handoff](docs/PROJECT_HANDOFF.md) (scope, architecture, acceptance criteria) and [the execution plan](docs/EXECUTION_PLAN.md). Where they conflict with `docs/handoff/`, the handoff directory wins.
4. Check and update [STATUS.md](docs/STATUS.md) before handing work to another contributor.
5. Use [the demo runbook](docs/DEMO.md) to verify the deliverable.

[CLAUDE.md](CLAUDE.md) directs Claude to the same shared rules. Technical references and the challenge's submission requirements are collected in [SOURCES.md](docs/SOURCES.md).

## Install, test, convert

These commands were run on Node 24.22.2 / npm 11.19.1:

```bash
npm install
```

```bash
npm test && npm run typecheck
```

```bash
echo '{"version":1,"source":"datadog","metrics":{}}' > /tmp/empty-mapping.json
npm run dev -- convert --source datadog --input fixtures/datadog/integrations-core/postgres --mapping /tmp/empty-mapping.json --out /tmp/conv1
```

`convert` writes `source/` (exact input copies), `dashboards/`, `rules/`, `check-rules/`, `report.json` and `manifest.json` under `--out`, which must be empty or absent. Options: `--format json|yaml` (default `json`) and `--project <name>` (default `datadog-migration`). Exit codes: `0` every item translated, `2` a partial or blocked item, `1` bad arguments, unreadable input, an invalid mapping or an unknown `--source`.

## The first deliverable

Fetch one selected Datadog dashboard and one selected metric monitor, compile the supported subset, export reviewable artifacts and a migration report, import both into Dash0, and show the resulting dashboard and check rule in the Dash0 UI. Include an unsupported example that produces a useful diagnostic.

Build a working vertical slice before expanding feature coverage. Business research, pricing, positioning, and pitch work are handled separately.

## Proposed implementation defaults

- TypeScript, Node.js 22+, npm, one CLI package.
- Commander for CLI arguments, Zod for boundary validation, Vitest for tests, tsx for local execution, tsc for type checking/building.
- Built-in `fetch` for HTTP. Small deterministic parser and explicit mapping files for translation.
- One `SourceProvider` interface selected with `--source` (only `datadog` for now). Each provider owns its own translation; shared code handles the report, manifest, and Dash0 import.
- JSON inputs/outputs first. No frontend, database, or hosted service. LLM involvement in conversion is prohibited, not merely unnecessary.

These are starting decisions for the empty repository. Preserve any existing implementation if the handoff is moved into a populated starter checkout. Record a justified stack change once in the handoff and status file.

## Setup to complete during implementation

- Resolve the required starter/fork relationship before the first implementation commit; see [the execution plan](docs/EXECUTION_PLAN.md#0-unblock-the-repository-and-destination).
- Use a dedicated feature branch, for example `codex/mvp-migrator`; keep module names and task names tool-neutral.
- Create EU Datadog and Dash0 accounts. The challenge requires a Dash0 organization name beginning with `thinc`.
- Copy `.env.example` to `.env` and supply credentials locally. Implement explicit `.env` loading or document shell environment setup; the template alone does not load variables.

No install or runtime command is advertised as working until it has been implemented and checked.
