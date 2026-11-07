"""Offline tests for the judge verifier (issue #87): scripts/render_judge_prompt.py and
scripts/run_oracle.py --mode judge / --out-dir. Every output goes to tmp_path."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
import yaml

import render_judge_prompt as rjp
import run_oracle as ro
import verify_common as vc
from tests.scripts.test_run_oracle import THREAD_A, THREAD_B, FakeAgent0, fenced, good_answer, no_sleep, started

KEY = "postgres/postgresql_dashboard"
ALL_KEYS = [s.key for s in ro.load_input(ro.DEFAULT_INPUT)]


def judge_job(key: str = KEY) -> ro.Job:
    return ro.Job(ro.CaseSpec(key, "default", "advanced", "high"), rjp.render(key), None)


def good_items(key: str = KEY) -> list[dict]:
    items = []
    for i, r in enumerate(ro.load_case(key)["requests"]):
        verdict = ("pass", "nit", "block")[i % 3]
        items.append(
            {
                "source_path": r["source_path"],
                "independent": "blocked: test" if i % 2 else ['sum({otel_metric_name="x", otel_metric_type="gauge"})'],
                "verdict": verdict,
                "checks": {c: "ok" for c in ro.JUDGE_CHECKS},
                "live_check": "2 series",
                "reasons": [] if verdict == "pass" else [f"reason {i}"],
            }
        )
    return items


def good_judge_answer(key: str = KEY) -> str:
    return yaml.safe_dump(good_items(key), sort_keys=False)


def requests_block(prompt: str) -> list[dict]:
    start = re.search(r"^Requests \(\d+\):\n", prompt, re.MULTILINE)
    end = prompt.index("\nOTel metric list (")
    return yaml.safe_load(prompt[start.end() : end])


# --------------------------------------------------------------------------- prompt


@pytest.mark.parametrize("key", ALL_KEYS)
def test_judge_prompt_is_deterministic_anonymous_and_complete(key: str) -> None:
    prompt = rjp.render(key)
    assert prompt == rjp.render(key)
    template_part = prompt[: prompt.index("\nRequests (")]  # units such as `{requests}` may follow
    placeholders = ("dataset", "title", "fixture", "template_variables", "request_count", "requests_block", "otel_candidates")
    assert not any("{" + p + "}" in template_part for p in placeholders)
    for word in ("agent0", "Agent0", "AGENT0", "provenance", "Provenance", "oracle"):
        assert word not in prompt

    case = vc.load_case(key)
    expected = {e["source_path"]: e for e in vc.load_expected(key)["requests"]}
    shown = requests_block(prompt)
    assert [r["source_path"] for r in shown] == [r["source_path"] for r in case["requests"]]
    for item in shown:
        assert item["candidate"] == vc.candidate(expected[item["source_path"]])
        ctx = vc.request_context(case, item["source_path"])
        assert item["datadog"] == {k: ctx[k] for k in ("widget", "queries", "formulas")}
    assert any(r["candidate"]["outcome"] == "blocked" for r in shown)
    assert "\n".join(ro.render_oracle_prompt.candidate_lines(case)) in prompt


def test_judge_prompt_states_the_rules_of_the_answer_prompt() -> None:
    answer_body = (ro.ROOT / "fixtures" / "oracle" / "PROMPT.md").read_text().split("\n---\n", 1)[1]
    rules = answer_body.split("Hard rules.", 1)[1].split("\n\nFor every request", 1)[0]
    assert "Hard rules." + rules in rjp.render(KEY)
    prompt = rjp.render(KEY)
    assert prompt.index("independent:") < prompt.index("verdict: pass | nit | block")
    assert "2026-11-07 14:00–15:25 UTC" in prompt


def test_cli_writes_the_prompt_under_the_verify_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(vc, "VERIFY_DIR", tmp_path)
    assert rjp.main([KEY]) == 0
    assert (tmp_path / KEY / "judge.prompt.md").read_text() == rjp.render(KEY)
    assert capsys.readouterr().out.startswith("Read-only task")


def test_recorded_judge_prompt_matches_the_renderer() -> None:
    recorded = vc.VERIFY_DIR / KEY / "judge.prompt.md"
    if not recorded.is_file():
        pytest.skip("no recorded judge prompt")
    assert recorded.read_text() == rjp.render(KEY)


# --------------------------------------------------------------------------- validator


def test_judge_validator_accepts_a_good_answer_and_normalizes_it() -> None:
    case = ro.load_case(KEY)
    items = good_items()
    items.reverse()  # answer order does not matter; results come back in case order
    items[0]["verdict"] = " BLOCK "
    items[0]["reasons"] = "single reason as a string"
    items[0]["checks"]["unit"] = "N/A"
    results = ro.validate_judge(yaml.safe_dump(items), case)
    assert [r["source_path"] for r in results] == [r["source_path"] for r in case["requests"]]
    last = results[-1]
    assert last["verdict"] == "block" and last["reasons"] == ["single reason as a string"] and last["checks"]["unit"] == "n/a"
    assert set(results[0]) >= {"source_path", "verdict", "reasons", "independent", "checks", "live_check"}


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda items: items.pop(0), "no verdict for 1"),
        (lambda items: items.append(dict(items[0])), "duplicate source_path"),
        (lambda items: items[0].update(source_path="widgets[99]"), "unknown source_path"),
        (lambda items: items[0].update(verdict="maybe"), "verdict must be one of"),
        (lambda items: items[1].update(reasons=[]), "nit needs at least one reason"),
        (lambda items: items[2].pop("reasons"), "block needs at least one reason"),
        (lambda items: items[0]["checks"].pop("grouping"), "checks missing"),
        (lambda items: items[0]["checks"].update(unit="fine"), "check values"),
        (lambda items: items[0].pop("checks"), "checks must be a mapping"),
        (lambda items: items[0].pop("independent"), "independent result missing"),
        (lambda items: items[0].pop("live_check"), "live_check missing"),
        (lambda items: items[1].update(reasons=[{"unit": "x"}]), "reasons must be a list of strings"),
    ],
)
def test_judge_validator_rejects_bad_answers(mutate, message: str) -> None:
    items = good_items()
    mutate(items)
    with pytest.raises(ro.AnswerError, match=message):
        ro.validate_judge(yaml.safe_dump(items), ro.load_case(KEY))


def test_judge_validator_rejects_non_lists() -> None:
    case = ro.load_case(KEY)
    with pytest.raises(ro.AnswerError, match="list of mappings"):
        ro.validate_judge("verdict: pass\n", case)
    with pytest.raises(ro.AnswerError, match="not valid YAML"):
        ro.validate_judge("- [unclosed", case)


# --------------------------------------------------------------------------- runner, judge mode


def judge_runner(tmp_path: Path, agent: object, **opts) -> tuple[ro.Runner, ro.State, list[str]]:
    state = ro.State(tmp_path / "state.judge.json")
    built: list[str] = []
    runner = ro.Runner(
        agent,  # type: ignore[arg-type]
        state,
        ro.Options(**{"backoff_s": 0, **opts}),
        mode=ro.get_mode("judge"),
        agent0_dir=tmp_path / "verify",
        sleep=no_sleep,
        log=lambda _: None,
        build=built.append,
    )
    return runner, state, built


def test_judge_run_writes_prompt_answer_and_readable_verdicts(tmp_path: Path) -> None:
    agent = FakeAgent0([started(THREAD_A)], [fenced(good_judge_answer(), THREAD_A)])
    runner, state, built = judge_runner(tmp_path, agent)
    job = judge_job()
    [result] = asyncio.run(runner.run([job]))

    assert result.status == "done" and "pass" in result.detail
    assert built == []
    out = tmp_path / "verify" / KEY
    assert (out / "judge.prompt.md").read_text() == job.prompt
    answer = (out / "judge.answer.yaml").read_text()
    assert answer.startswith("# Agent0 judge answer, verbatim.") and THREAD_A in answer.splitlines()[0]
    assert "judge.prompt.md" in answer.splitlines()[0] and answer.endswith(good_judge_answer())

    verdicts = vc.read_verdicts(KEY, "judge", path=out / "judge.yaml")
    assert verdicts is not None
    assert list(verdicts) == [r["source_path"] for r in ro.load_case(KEY)["requests"]]
    first = next(iter(verdicts.values()))
    assert first["checks"] == {c: "ok" for c in ro.JUDGE_CHECKS} and first["live_check"] == "2 series"
    assert first["independent"]
    doc = yaml.safe_load((out / "judge.yaml").read_text())
    assert doc["meta"] == {
        "thread_id": THREAD_A,
        "asked_at": state.get(KEY)["asked_at"],
        "prompt_sha256": ro.prompt_sha256(job.prompt),
        "dataset": "default",
        "model_class": "advanced",
        "reasoning_effort": "high",
    }
    assert state.get(KEY)["status"] == "done" and not (tmp_path / "agent0").exists()


def test_invalid_judge_answer_retries_on_a_new_thread(tmp_path: Path) -> None:
    bad = good_items()
    bad[1]["reasons"] = []
    agent = FakeAgent0(
        [started(THREAD_A), started(THREAD_B)],
        [fenced(yaml.safe_dump(bad), THREAD_A), fenced(good_judge_answer(), THREAD_B)],
    )
    runner, state, _ = judge_runner(tmp_path, agent)
    [result] = asyncio.run(runner.run([judge_job()]))
    assert result.status == "done" and len(agent.start_calls) == 2
    assert state.get(KEY)["thread_id"] == THREAD_B and state.get(KEY)["attempts"] == 2


def test_mode_defaults_follow_the_module_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ro, "VERIFY_DIR", tmp_path / "verify")
    monkeypatch.setattr(ro, "DEFAULT_JUDGE_STATE", tmp_path / "verify" / "state.judge.json")
    judge = ro.get_mode("judge")
    assert judge.out_dir == tmp_path / "verify" and judge.state_path == tmp_path / "verify" / "state.judge.json"
    assert not judge.builds and judge.needs_expected
    answer = ro.get_mode("answer")
    assert answer.out_dir == ro.AGENT0_DIR and answer.state_path == ro.DEFAULT_STATE and answer.builds
    r2 = ro.get_mode("answer", tmp_path / "r2")
    assert r2.out_dir == tmp_path / "r2" and r2.state_path == tmp_path / "r2" / "state.json" and not r2.builds
    with pytest.raises(ro.InputError):
        ro.get_mode("nope")


# --------------------------------------------------------------------------- CLI (fake MCP client)


def write_input(tmp_path: Path, keys: list[str]) -> Path:
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"dataset": "default", "dashboards": [{"case": k} for k in keys]}))
    return path


@pytest.fixture
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> dict:
    """main() with a fake Agent0 and a recorder in place of build_expected."""
    holder: dict = {"built": []}
    monkeypatch.setenv("DASH0_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("DASH0_MCP_URL", "http://127.0.0.1:9/mcp")
    monkeypatch.setattr(ro.McpAgent0, "over_http", lambda *a, **k: holder["agent"])
    monkeypatch.setattr(ro, "build_expected_for", holder["built"].append)
    monkeypatch.setattr(ro.Runner, "__init__", _no_backoff(ro.Runner.__init__))
    return holder


def _no_backoff(init):
    def wrapped(self, *args, **kwargs):
        kwargs["sleep"] = no_sleep
        kwargs["log"] = lambda _: None
        init(self, *args, **kwargs)

    return wrapped


def test_out_dir_writes_there_and_never_builds(tmp_path: Path, fake_mcp: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    key = "nginx/NGINX-Metrics_dashboard"
    sentinel = tmp_path / "agent0-default"
    monkeypatch.setattr(ro, "AGENT0_DIR", sentinel)
    monkeypatch.setattr(ro, "DEFAULT_STATE", sentinel / "state.json")
    fake_mcp["agent"] = FakeAgent0([started(THREAD_A)], [fenced(good_answer(key), THREAD_A)])
    out = tmp_path / "agent0-r2"

    assert ro.main([str(write_input(tmp_path, [key])), "--mode", "answer", "--full", "--out-dir", str(out)]) == 0
    assert (out / key / "answer.yaml").read_text().endswith(good_answer(key))
    assert (out / key / "prompt.md").read_text() == ro.render_oracle_prompt.render(key)
    assert json.loads((out / "state.json").read_text())["cases"][key]["status"] == "done"
    assert fake_mcp["built"] == [] and not sentinel.exists()


def test_answer_mode_cli_still_builds(tmp_path: Path, fake_mcp: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    key = "nginx/NGINX-Metrics_dashboard"
    monkeypatch.setattr(ro, "AGENT0_DIR", tmp_path / "agent0")
    monkeypatch.setattr(ro, "DEFAULT_STATE", tmp_path / "agent0" / "state.json")
    fake_mcp["agent"] = FakeAgent0([started(THREAD_A)], [fenced(good_answer(key), THREAD_A)])
    assert ro.main([str(write_input(tmp_path, [key])), "--mode", "answer"]) == 0
    assert fake_mcp["built"] == [key] and (tmp_path / "agent0" / key / "answer.yaml").is_file()


def test_judge_mode_cli_uses_the_verify_dir(tmp_path: Path, fake_mcp: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ro, "VERIFY_DIR", tmp_path / "verify")
    monkeypatch.setattr(ro, "DEFAULT_JUDGE_STATE", tmp_path / "verify" / "state.judge.json")
    fake_mcp["agent"] = FakeAgent0([started(THREAD_A)], [fenced(good_judge_answer(), THREAD_A)])
    assert ro.main([str(write_input(tmp_path, [KEY])), "--mode", "judge"]) == 0
    assert vc.read_verdicts(KEY, "judge", path=tmp_path / "verify" / KEY / "judge.yaml")
    assert json.loads((tmp_path / "verify" / "state.judge.json").read_text())["cases"][KEY]["status"] == "done"
    assert fake_mcp["built"] == []


def test_judge_needs_expected_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(ro, "EXPECTED_DIR", tmp_path / "expected")
    monkeypatch.setattr(ro, "DEFAULT_JUDGE_STATE", tmp_path / "state.judge.json")
    assert ro.main([str(write_input(tmp_path, [KEY])), "--mode", "judge", "--case", KEY, "--dry-run"]) == 2
    assert "no fixtures/expected" in capsys.readouterr().err


def test_dry_runs_print_the_plan_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("DASH0_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(ro, "DEFAULT_JUDGE_STATE", tmp_path / "state.judge.json")
    assert ro.main(["--mode", "judge", "--dry-run", "--case", KEY]) == 0
    out = capsys.readouterr().out
    assert "mode judge" in out and "build_expected: no" in out and f"queued {KEY}" in out

    r2 = tmp_path / "r2"
    assert ro.main(["--mode", "answer", "--full", "--out-dir", str(r2), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "build_expected: no" in out and out.count("queued ") == len(ALL_KEYS)
    assert not r2.exists()


def test_default_runs_author_judge_haiku_then_triage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import crosscheck
    import triage

    calls: list[object] = []
    monkeypatch.setattr(ro, "run_mode", lambda args, name, refresh_stale=False: calls.append((name, refresh_stale)) or 0)
    monkeypatch.setattr(crosscheck, "main", lambda argv: calls.append(("haiku", argv)) or 0)
    monkeypatch.setattr(triage, "main", lambda argv: calls.append(("triage", argv)) or 0)
    assert ro.main(["--case", KEY]) == 0
    assert calls == [("answer", False), ("judge", True), ("haiku", ["input", KEY, "--retries", "3"]), ("triage", [])]

    calls.clear()
    assert ro.main([str(write_input(tmp_path, [KEY])), "--dry-run"]) == 0
    assert calls[2] == ("haiku", ["input", KEY, "--retries", "3", "--dry-run"]) and len(calls) == 3  # no triage


def test_default_stops_on_setup_error_and_rejects_out_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ro, "run_mode", lambda args, name, refresh_stale=False: calls.append(name) or 2)
    assert ro.main(["--case", KEY]) == 2 and calls == ["answer"]
    assert ro.main(["--out-dir", "x"]) == 2
