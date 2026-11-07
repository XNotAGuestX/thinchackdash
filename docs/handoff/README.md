# Handoff overview: Datadog → Perses converter

**Target:** a deterministic CLI that converts Datadog dashboards into **Perses** dashboards (the open-source standard), with a per-item migration report.

- Dash0 is where results are checked against real telemetry, and the optional publish target.
- Monitors → Prometheus rules and Dash0 check rules are secondary.
- **No LLM takes part in any conversion step** (`AGENTS.md`).

This directory is the implementation plan, split into independent handoffs. Each numbered file is a self-contained plan that ends in working, tested software. Read `00-context.md` first; it's the shared spec every plan argues from.

## Which handoff holds what

| File | Holds | Deliverable | Depends on | Blocked by | Status |
|---|---|---|---|---|---|
| [00-context.md](00-context.md) | **Shared spec:** goal, rules, decisions (D1–D6, A1–A4), open blockers (B1–B3), module layout, stack, Datadog and Perses/Dash0 facts, golden widget table, outcome model, diagnostic code registry, exit codes | — (reference) | — | — | Current |
| [01-bootstrap-cli.md](01-bootstrap-cli.md) | Package, `convert` CLI, file and folder input, `SourceProvider` table, asset recognition, pinned Postgres fixtures, writers, report and manifest, determinism | `convert` runs offline and accounts for every widget and monitor (all still `blocked`) | — | — | Done (2026-11-07) |
| [02-query-parser.md](02-query-parser.md) | Bounded Datadog metric query grammar and parser, with full input consumption | `parseMetricQuery` with the golden corpus passing | 01 | — | Not started |
| [03-mapping-promql.md](03-mapping-promql.md) | Mapping file contract (Zod), PromQL selector rendering and escaping, `compileMetricQuery` check order, Postgres golden mapping | Golden query → `avg by (postgresql_database_name) ({otel_metric_name="postgresql.backends", …})` | 01, 02 | A human review of the mapping entries | Tasks 1–2 done (S2); Tasks 3–4 open |
| [04-perses-dashboard.md](04-perses-dashboard.md) | **The core converter:** timeseries widgets → Perses panels and grid layout, per-widget outcomes, output validation (Zod + Lezer PromQL), golden "Postgres - Metrics" run | `dashboards/postgres-metrics.json` (1/9 panels) and an exact report. **The MVP converter is done here.** | 01–03 | — | Not started |
| [05-dashboard-coverage.md](05-dashboard-coverage.md) | Coverage ladder: default aggregator, `rate` kind and fixed matchers, scale, series grain (dedup), arithmetic formulas, template variables, `note`/`query_value`/`group`, legends, golden P1 | "Postgres - Metrics" at 6/9 panels; "Overview" partial | 04 | 07 Task 1 (Dash0 render check of `Markdown`, `StatChart`, `ListVariable`) | Not started |
| [06-monitors.md](06-monitors.md) | Monitor query parser, options allowlist, `expression` mapping kind (deduplicated connection ratio), Prometheus rule group and Dash0 check-rule renderers, monitor golden | `percent_usage_connections` translated; `replication_delay` blocked | 01–03 | A3 confirmation | Not started |
| [07-dash0-publish.md](07-dash0-publish.md) | Live contract check, Dash0 client, receipts, `publish` (dry-run, create-only, no blind retry), live evidence | Dashboard and check rule imported, read back and visible in Dash0 | 04, 06 | **B1** token; user approval for live writes | Not started |
| [08-more-services.md](08-more-services.md) | Kafka (2 translatable monitors), Redis, nginx, Docker: pinned assets, mapping files, goldens | One reviewed golden per service | 04, 05 (06 for monitors) | Human mapping review | Not started |
| [09-demo-and-submission.md](09-demo-and-submission.md) | **Not converter work:** durable demo telemetry, connection-holder script for the threshold crossing, DEMO and README rewrite, fork and PR | A rehearsed demo and the submitted PR | 07 | **B2** demo owner; user request for the PR | Not started |
| [10-datadog-api-fetch.md](10-datadog-api-fetch.md) | `fetch` by dashboard and monitor ID, feeding the same `convert` path | Fetched folder converts byte-identically to the file input | 01 (04 and 05 Task 0 for the round-trip test) | **B3** Datadog account | Not started |

**Background material**, not plans: [../DASH0_INVENTORY.md](../DASH0_INVENTORY.md) (what data exists in Dash0, and the service order) and [../PLANNING_HANDOFF.md](../PLANNING_HANDOFF.md) (research notes from the planning sessions).

## Order of work

```text
01 → 02 → 03 → 04  ← MVP converter (offline, no credentials needed)
                 ├─→ 05 → 08
                 ├─→ 06 ─┐
                 │       ├─→ 07 (needs B1) → 09 (needs B2)
                 └───────┘
01 → 10 (needs B3; independent)
```

- Two people? Use [WORK_SPLIT.md](WORK_SPLIT.md): two parallel tracks, sync points and file ownership.
- Plans 02 and 03 are sequential, because 03 consumes the parser's model.
- Plans 05 and 06 can run in parallel after 04; they touch different files apart from `mapping.ts` and `compile-query.ts`, so coordinate those two.
- 07 Task 1 (the live contract check) is worth doing as soon as **B1** arrives, because it unblocks the plan 05 gates.

## Open decisions and blockers

These are also in `00-context.md` §3. Answer them in free text.

- **A1** Target vocabulary = names and labels exactly as found in Dash0. *Assumed default.*
- **A2** Alert output = Prometheus rule group + Dash0 check-rule body. *Assumed default.*
- **A3** Monitor strictness = approximate and warn on a reviewed allowlist; block the rest. *Assumed default.*
- **B1** A Dash0 API token, or a UI dashboard export. **B2** A demo telemetry owner. **B3** A Datadog account (plan 10 only).

## Where the older docs conflict, this directory wins

| Older doc says | This directory says |
|---|---|
| `PROJECT_HANDOFF.md`: first example is `demo.temperature` | The first example is integrations-core Postgres ("Postgres - Metrics" + `percent_usage_connections`) |
| Commands `compile` and `import` | `convert` and `publish` (A4) |
| Folder input is P1; YAML is P2 | Both are part of plan 01 and plan 04 (user decisions D3 and D4) |
| `EXECUTION_PLAN.md` phases 0–5 | Plans 01–10 above; repo setup and the fork are in plan 09 Task 5 |
| `PLANNING_HANDOFF.md` §6 used `db_namespace` | The real label is `postgresql_database_name` (corrected; see `00-context.md` §7) |

## How to take a handoff

1. Read `AGENTS.md`, this file, `00-context.md`, then the plan.
2. Claim the task's [GitHub issue](https://github.com/XNotAGuestX/thinchackdash/issues) (assign it, comment the branch name). Each plan task has one issue; each plan has an epic.
3. Work task by task on your own branch. Steps are checkboxes, and each task ends with a commit and a PR that closes its issue; XNotAGuestX reviews it, and Felix or his agent merges it.
4. At handoff, comment on the issue with the exact check results; when a plan finishes, update this table's Status column and `docs/STATUS.md`.

Recommended execution: run plans 01–04 with **subagent-driven development**. Their tasks have tight interfaces (shared types, codes, exact PromQL strings), so a reviewer per task catches drift early. The later plans can run natively.
