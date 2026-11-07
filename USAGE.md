# Usage: testing and improvement loop

- `uv run pytest` runs every human-reviewed or triage-green case; add `--include-unreviewed` to run the rest too.
- `uv run pytest --html=out/test-report.html --self-contained-html && open out/test-report.html` shows every test in the browser, filterable by passed, failed and skipped (unreviewed).
- `scripts/pin-integrations-core.sh` then `uv run scripts/extract_cases.py` refresh the Datadog source cases (pick dashboards in `fixtures/oracle/dashboards.json`).
- `uv run scripts/run_oracle.py` is the default test creation (needs `DASH0_AUTH_TOKEN` and `ANTHROPIC_API_KEY`): Agent0 writes the expectations (`fixtures/expected/**/expected.yaml`), Agent0 reviews them, Haiku reviews them, and `scripts/triage.py` colours every request into `fixtures/expected/TRIAGE.md` plus `verified.json` (green requests count as tests); `--dry-run` shows the plan, `--mode answer` or `--mode judge` runs one step.
- The checkers are not tested by default; `uv run scripts/mutate_expected.py --all`, `uv run scripts/crosscheck.py input --all --mutations` and `uv run scripts/triage.py --catch-rates` measure them on known-bad expectations and distrust weak ones.
- `uv run scripts/lint_expected.py --all` is an optional free, deterministic extra check.
- Humans verify as much as possible, red and yellow first, by setting `reviewed: true` (or a correction) in `fixtures/expected/<svc>/<id>/overrides.yaml`; `uv run scripts/render_review_dashboard.py --all` helps by rendering both sides as Perses dashboards.
- `DASH0_AUTH_TOKEN=x PERSES_PORT=18080 docker compose -f scripts/review/docker-compose.yaml up -d` starts a local Perses viewer, and `uv run scripts/review/load_local.py --perses-url http://localhost:18080` loads the rendered dashboards and prints their URLs.
- A real Dash0 token is only needed for the charts to plot live data, since Perses proxies each panel's PromQL to Dash0; with a placeholder you still see every panel, query and note.
- After every `pytest` run that executes request cases, open `out/test-report/index.html` (the path is printed at the end) to compare expected and actual PromQL, outcome and diagnostic codes per request in the browser; failures come first, with a canonical PromQL diff.
- A failing test means the converter or the expectation is wrong: fix the converter in `src/migrator`, or correct the expectation in `overrides.yaml`, then rerun `uv run pytest`.
