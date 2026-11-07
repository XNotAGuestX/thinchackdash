"""Offline tests for scripts/triage.py (review priority from verifier verdicts)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import triage
import verify_common as vc

KEY = "postgres/postgresql_dashboard"


@pytest.fixture(autouse=True)
def tmp_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    verify = tmp_path / "verify"
    monkeypatch.setattr(vc, "VERIFY_DIR", verify)
    monkeypatch.setattr(vc, "MUTATIONS_DIR", verify / "mutations")
    return verify


def paths(key: str = KEY) -> list[str]:
    return [r["source_path"] for r in vc.load_case(key)["requests"]]


def write(verifier: str, verdicts: dict[str, str], key: str = KEY) -> None:
    results = []
    for sp, v in verdicts.items():
        results.append({"source_path": sp, "verdict": v, "reasons": [f"{verifier} says {v}"] if v != "pass" else []})
    vc.write_verdicts(key, verifier, results, {"tool": "test"})


def write_mutations(tmp_verify: Path, n: int, structural: int, key: str = KEY) -> list[dict[str, Any]]:
    sps = paths(key)
    muts = [
        {"id": f"m{i:02d}", "source_path": sps[i % len(sps)], "mutation": "swap_metric" if i % 2 else "drop_matcher",
         "structural": i < structural, "expected": {"outcome": "blocked", "errors": ["MISSING_METRIC_MAPPING"]}}
        for i in range(n)
    ]
    path = tmp_verify / "mutations" / f"{key}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(muts, sort_keys=False))
    return muts


def write_mutation_verdicts(verifier: str, muts: list[dict[str, Any]], caught: set[str], key: str = KEY) -> None:
    results = [
        {"source_path": m["id"], "verdict": "block" if m["id"] in caught else "pass", "reasons": ["caught"] if m["id"] in caught else []}
        for m in muts
    ]
    vc.write_verdicts(key, verifier, results, {"tool": "test"}, mutations=True)


# --------------------------------------------------------------------------- priority rules


@pytest.mark.parametrize(
    ("signals", "untrusted", "want"),
    [
        ({}, set(), "unverified"),
        ({"lint": "pass"}, set(), "unverified"),
        ({"lint": "pass", "rerun": "pass"}, set(), "unverified"),
        ({"lint": "block"}, set(), "red"),
        ({"lint": "pass", "judge": "pass", "haiku": "block"}, set(), "red"),
        ({"lint": "nit"}, set(), "yellow"),
        ({"rerun": "nit", "judge": "pass"}, set(), "yellow"),
        ({"judge": "pass", "haiku": "nit"}, set(), "yellow"),  # split between judge and haiku
        ({"judge": "pass"}, set(), "green"),
        ({"haiku": "pass", "lint": "pass", "rerun": "pass"}, set(), "green"),
        ({"judge": "block", "haiku": "pass"}, {"judge"}, "red"),  # an untrusted verifier's block still counts
        ({"judge": "pass", "haiku": "nit"}, {"haiku"}, "yellow"),  # an untrusted verifier's nit still counts
        ({"judge": "block"}, {"judge"}, "red"),
        ({"judge": "pass"}, {"judge"}, "unverified"),  # an untrusted pass is not evidence for green
        ({"judge": "pass", "haiku": "pass"}, {"haiku"}, "green"),  # the trusted judge's pass is enough
        ({"lint": "block", "judge": "pass"}, {"judge"}, "red"),
    ],
)
def test_priority_rules(signals: dict[str, str], untrusted: set[str], want: str) -> None:
    assert triage.priority(signals, untrusted) == want


def test_priorities_cover_every_request_in_case_order() -> None:
    sps = paths()
    write("lint", {sps[0]: "block", sps[1]: "pass", sps[2]: "pass"})
    write("judge", {sps[1]: "pass", sps[2]: "nit"})
    rows = triage.priorities(KEY)
    assert list(rows) == sps
    assert rows[sps[0]] == {"priority": "red", "signals": {"lint": "block"}, "reasons": {"lint": ["lint says block"]}, "untrusted": []}
    assert rows[sps[1]]["priority"] == "green"
    assert rows[sps[2]]["priority"] == "yellow"
    assert rows[sps[2]]["reasons"] == {"judge": ["judge says nit"]}
    assert all(rows[sp]["priority"] == "unverified" and rows[sp]["signals"] == {} for sp in sps[3:])


def test_untrusted_verifier_findings_still_count(tmp_verify: Path) -> None:
    muts = write_mutations(tmp_verify, 10, 0)
    write_mutation_verdicts("judge", muts, {m["id"] for m in muts[:7]})  # 70% < 80%
    sps = paths()
    write("judge", {sps[0]: "block"})
    write("haiku", {sps[0]: "pass"})
    row = triage.priorities(KEY, rates=triage.catch_rates(vc.all_keys()))[sps[0]]
    assert row["priority"] == "red"
    assert row["signals"] == {"judge": "block", "haiku": "pass"}
    assert row["untrusted"] == ["judge"]


def test_low_rate_with_few_mutations_stays_trusted(tmp_verify: Path) -> None:
    muts = write_mutations(tmp_verify, 9, 0)
    write_mutation_verdicts("judge", muts, set())  # 0% but not measured (< 10)
    sps = paths()
    write("judge", {sps[0]: "block"})
    assert triage.priorities(KEY, rates=triage.catch_rates(vc.all_keys()))[sps[0]]["priority"] == "red"


def test_verifiers_are_not_tested_by_default(tmp_path: Path, tmp_verify: Path) -> None:
    muts = write_mutations(tmp_verify, 10, 0)
    write_mutation_verdicts("judge", muts, set())  # 0% catch rate, measured
    sps = paths()
    write("judge", {sps[0]: "pass"})
    assert triage.priorities(KEY)[sps[0]]["priority"] == "green"
    assert triage.priorities(KEY, rates=triage.catch_rates(vc.all_keys()))[sps[0]]["priority"] == "unverified"
    out = tmp_path / "t.md"
    assert triage.main(["--out", str(out)]) == 0
    assert "Not measured (default" in out.read_text()


# --------------------------------------------------------------------------- catch rates


def test_catch_rates_arithmetic(tmp_verify: Path) -> None:
    muts = write_mutations(tmp_verify, 10, 4)  # m00..m03 structural; even ids drop_matcher, odd swap_metric
    write_mutation_verdicts("lint", muts, {"m00", "m01", "m02", "m03", "m04", "m05", "m06", "m07", "m08"})
    write_mutation_verdicts("haiku", muts[:4], {"m01"})
    rates = triage.catch_rates(vc.all_keys())
    assert set(rates) == {"lint", "haiku"}
    lint = rates["lint"]
    assert (lint["caught"], lint["total"]) == (9, 10)
    assert lint["rate"] == pytest.approx(0.9)
    assert lint["structural_rate"] == pytest.approx(1.0)
    assert lint["by_mutation"] == {
        "drop_matcher": {"caught": 5, "total": 5, "rate": 1.0},
        "swap_metric": {"caught": 4, "total": 5, "rate": pytest.approx(0.8)},
    }
    haiku = rates["haiku"]
    assert (haiku["caught"], haiku["total"], haiku["rate"]) == (1, 4, 0.25)
    assert haiku["structural_rate"] == pytest.approx(0.25)
    assert triage.untrusted_verifiers(rates) == set()  # haiku is below the threshold but not measured


def test_catch_rates_empty_without_mutations() -> None:
    assert triage.catch_rates(vc.all_keys()) == {}


# --------------------------------------------------------------------------- TRIAGE.md


def test_triage_md_is_deterministic_and_ordered(tmp_path: Path, tmp_verify: Path) -> None:
    sps = paths()
    write("lint", {sps[0]: "pass", sps[1]: "block", sps[2]: "nit"})
    write("judge", {sps[0]: "pass"})
    muts = write_mutations(tmp_verify, 10, 2)
    write_mutation_verdicts("lint", muts, {m["id"] for m in muts})
    out1, out2 = tmp_path / "a.md", tmp_path / "b.md"
    assert triage.main(["--catch-rates", "--out", str(out1)]) == 0
    assert triage.main(["--catch-rates", "--all", "--out", str(out2)]) == 0
    text = out1.read_text()
    assert text == out2.read_text()
    assert "count as verified" in text and "reviewed: true" in text and "overrides.yaml" in text
    assert "| lint | 10 / 10 | 100% | 2 / 2 (100%) | yes |" in text
    section = text.split(f"## {KEY}\n", 1)[1].split("\n## ", 1)[0]
    order = [line.split("`")[1] for line in section.splitlines() if line.startswith("- **")]
    assert order[:3] == [sps[1], sps[2], sps[3]]  # red, yellow, then unverified in case order
    assert order[-1] == sps[0]  # green last
    assert "lint: lint says block" in section
    assert 'avg:postgresql.rows_fetched{$scope}' in section
    assert 'postgresql.tup_fetched' in section


def test_triage_md_for_selected_keys(tmp_path: Path) -> None:
    out = tmp_path / "t.md"
    assert triage.main([KEY, "--out", str(out)]) == 0
    text = out.read_text()
    assert f"## {KEY}" in text and "## nginx/" not in text
