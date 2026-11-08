# Broad integration compatibility test

Recorded 2026-11-08 for [#169](https://github.com/XNotAGuestX/thinchackdash/issues/169).

The existing converter was tested against **all 845 dashboard/monitor JSON files from 253 integrations** at the project's pinned [DataDog/integrations-core commit](https://github.com/DataDog/integrations-core/tree/a9b9e42b7e484861ecbd1f5919da7c52a694b74d). This adds 248 integrations to the previous five-integration fixture set. Conversion code and mappings were unchanged.

The broad result is limited: **116 non-note panels emit metric queries**, across 24 dashboards and 18 integrations. Most additional converted content is static notes. A dashboard that emits only notes is not a functional metric migration.

## Scope and checks

- Initial compiler: `207557f1d968162c9c18ef482ab0cb006f8116b9` (main after PR #166). Latest-main confirmation: `ea029f50c47f43e0a5cebf63386b323b8964791b` (after PR #168); conversion totals are unchanged.
- Source: `a9b9e42b7e484861ecbd1f5919da7c52a694b74d`.
- Selection: every Git blob ending in `.json` beneath `<integration>/assets/dashboards/` or `<integration>/assets/monitors/`, including nested paths. The recursive upstream tree was not truncated. No integration allowlist or success-based filtering was used.
- Inputs: 516 dashboard files and 329 monitor files; all 845 were recognized, none skipped. All source blobs and the original BSD-3-Clause license were downloaded read-only and verified against their pinned Git object hashes.
- Mapping input: the complete `mappings/datadog` directory. No new mappings, assumptions, review flags or query rules were added for this test.
- Both complete conversion runs exited **2**, reflecting partial/blocked outcomes. There were no conversion exceptions.
- All **1,328 output files**, totaling **77,119,291 bytes per run**, matched byte-for-byte across the two runs. Every copied source matched its original bytes; every manifest hash matched its emitted file.
- Emitted syntax/schema validation passed. API acceptance and semantic validation remained `not_run`; no vendor writes or live queries were performed.
- `uv run migrator check`: Ruff and strict mypy (86 files) passed; **971 passed, 621 skipped** (unreviewed expectations), in 24.83 seconds, using uv-managed CPython 3.13.18 and `uv.lock`.

### Latest-main confirmation

Felix asked whether the test included the best assumptions and new fixes. The original test already included approved `assumed`, `approximated` and `best_effort` outcomes: derived/semantic mappings, TOPN, TIMEPIN, monitor arithmetic and the bounded weighted/rollup/as-rate functions. There was no exact-only filter.

PR #168 merged afterward, so the complete cached corpus was rerun twice on `ea029f5`. All **1,328 files / 79,884,314 bytes** matched across those latest runs. Every source, destination payload and manifest was also byte-identical to the original `207557f` run. Existing report fields were unchanged; the new `mappingCoverage` field was added. Latest checks: Ruff and strict mypy (88 files) passed; **993 passed, 621 skipped**, in 26.93 seconds. Evidence is under `/private/tmp/thinchackdash-broad-latest-ea029f5/out/broad-latest/`.

## Conversion categories

These are compiler-reported widget units. Non-note widgets include unsupported logs, status views and other visualization types as well as metric charts. The strongest applicable category is reported, with every remark retained: `best_effort > assumed > approximated > translated`.

| Category | All widgets | Non-note widgets | Static notes | Emitted PromQL expressions |
|---|---:|---:|---:|---:|
| Translated | 1,811 | 94 | 1,717 | 154 |
| Approximated | 10 | 10 | 0 | 12 |
| Assumed | 8 | 8 | 0 | 8 |
| Best effort | 4 | 4 | 0 | 4 |
| **Converted** | **1,833** | **116** | **1,717** | **178** |
| Blocked | 12,451 | 12,433 | 18 | — |
| Total | 14,284 | 12,549 | 1,735 | — |

Expressions are grouped by their parent widget's category. They are distinct from raw query strings and displayed series. No converted non-note panel had an empty query list or only constant expressions.

Dashboard results: **24** emitted metric-query panels, **451** emitted only notes, and **41** emitted no panels. All 516 dashboards were partial (475) or blocked (41); none fully converted. Of the 329 monitors, **3 converted as approximated and 326 blocked**. All three converted monitors were already in the original fixture integrations.

## What the added integrations contribute

The additional 248 integrations contain 507 dashboards and 316 monitors. They contribute **41 converted non-note panels: 39 translated and 2 approximated**. Their other 12,304 non-note widgets and all 316 monitors block. They also emit 1,670 static notes, counted separately.

Thirteen additional integrations emit any metric panel. Twelve reuse existing **`system.*` host mappings**: BentoML, Btrfs, Couchbase, Elasticsearch, IIS, Kubernetes, Kyoto Tycoon, Memcached, MySQL, Riak, SQL Server and Tomcat. The remaining integration, ZooKeeper, emits two native `zookeeper.*` panels. This does not establish support for MySQL-, Kubernetes- or other product-specific metrics.

Selected results below exclude static notes from both the converted count and denominator. Complete results for all 253 integrations are retained in the ignored local evidence.

| Integration | Dashboard files | Non-note widgets converted / total | Converted categories | Monitors converted / total |
|---|---:|---:|---|---:|
| MySQL | 2 | 6 / 33 | translated | 0 / 2 |
| Memcached (`mcache`) | 2 | 6 / 29 | translated | 0 / 0 |
| SQL Server | 3 | 4 / 55 | translated | 0 / 6 |
| Elasticsearch | 2 | 3 / 40 | translated | 0 / 6 |
| Tomcat | 2 | 3 / 24 | translated | 0 / 6 |
| IIS | 2 | 3 / 37 | translated | 0 / 3 |
| Couchbase | 1 | 3 / 9 | translated | 0 / 0 |
| ZooKeeper (`zk`) | 1 | 2 / 16 | approximated (TIMEPIN) | 0 / 0 |
| Kubernetes | 12 | 1 / 394 | translated | 0 / 8 |
| MongoDB (`mongo`) | 1 | 0 / 26 | blocked | 0 / 8 |
| Apache | 1 | 0 / 12 | blocked | 0 / 2 |
| HAProxy | 3 | 0 / 62 | blocked | 0 / 11 |
| RabbitMQ | 2 | 0 / 62 | blocked | 0 / 6 |
| Cassandra | 5 | 0 / 160 | blocked | 0 / 0 |
| Consul | 1 | 0 / 16 | blocked | 0 / 1 |
| etcd | 2 | 0 / 40 | blocked | 0 / 0 |
| Vault | 2 | 0 / 83 | blocked | 0 / 1 |
| ClickHouse | 1 | 0 / 43 | blocked | 0 / 0 |
| Envoy | 2 | 0 / 66 | blocked | 0 / 1 |

## Blockers and parser coverage

| Widget diagnostic | Affected widget units |
|---|---:|
| Unsupported formula | 4,646 |
| Unsupported data source | 4,087 |
| Unsupported widget type/option | 3,528 |
| Missing metric mapping | 2,867 |
| Unsupported query function | 1,022 |
| Unsupported query syntax | 476 |
| Missing label mapping | 29 |
| Unsupported template variable | 4 |

Counts overlap; a widget can have multiple errors. The compiler can stop before reaching another blocker, so these are observed failures, not projected gains. Log/event/security-product dashboards are included in this broad corpus and commonly fail the current metrics-only data-source restriction.

Diagnostics reported **2,692 distinct missing metric names**. This is a lower bound on coverage gaps, because unsupported types, data sources or formulas can prevent metric compilation. Existing shared host mappings explain most new metric-panel conversions; this test adds no product mappings.

Inspecting every raw dashboard metric query independently of widget blockers makes the main bottleneck clearer:

| Raw query status | Occurrences |
|---|---:|
| Complete parse and a metric mapping exists | 314 |
| Complete parse but no metric mapping | 6,798 |
| Unsupported complete parse | 1,098 |
| Total | 8,210 |

The mapping catalogue has **92 entries** across Docker, Kafka, nginx, Postgres, Redis, host and ZooKeeper metrics. It has no native MySQL, MongoDB, Apache, RabbitMQ, Cassandra, Kubernetes or SQL Server mappings. There are **4,388 distinct parseable unmapped dashboard metric names**. This identifies missing implementation coverage; it does not establish that those metrics have no possible OTel equivalent. Even the 314 mapped occurrences can still fail label/function/formula checks, and an all-or-nothing widget fails if any displayed series is unsupported.

The complete metric-query parser was also called on **8,210 raw dashboard metric-query occurrences** from 197 integrations: **7,112 parsed**, **1,098 returned unsupported diagnostics**, and **none raised an exception**. Unsupported results split into 904 syntax errors and 194 wrapping-function errors. There were 7,274 distinct query strings. A successful parse establishes syntax support only; suffix functions, mappings and widget behavior can still block conversion.

## Reproduction and evidence

Source preparation used the official recursive tree and unchanged raw blobs at the pinned commit. The Git blob check is `sha1(b"blob " + ascii(len(data)) + b"\0" + data) == tree_entry.sha`; the selected asset set is exactly the predicate above. The original source license is retained with the local corpus.

Actual conversion commands, run from the isolated branch worktree after `uv sync --locked --python-preference only-managed`:

```sh
uv run migrator convert --source datadog \
  --input out/broad-integrations/source --mapping mappings/datadog \
  --out out/broad-integrations/run-1
uv run migrator convert --source datadog \
  --input out/broad-integrations/source --mapping mappings/datadog \
  --out out/broad-integrations/run-2
uv run migrator check
```

Ignored evidence at `/private/tmp/thinchackdash-169/out/broad-integrations/` includes the source manifest and license, both compiler reports/manifests and original source copies, exact fetch/analysis tools, per-integration Markdown/JSON, missing metric names and deterministic byte comparisons. It contains public test inputs and local run artifacts and is intentionally not committed. The source manifest provides the exact paths and hashes needed to reconstruct the test from the pinned upstream repository.

This is an offline compatibility result. It proves repeatability and the recorded syntax/schema checks, and exposes broad coverage gaps. It does not prove destination import/rendering, telemetry availability or numerical equivalence.
