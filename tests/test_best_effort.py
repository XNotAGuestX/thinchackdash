"""Best-effort results remain visible and publishable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from migrator.convert import ConvertOptions, run_convert
from migrator.publish import PublishOptions, run_publish
from migrator.report import exit_code_for, format_summary, manifest_entry, summarize
from migrator.target import Diagnostic, DiagnosticCode, ItemResult, remark_outcome


@pytest.mark.parametrize("codes", [
    ["BEST_EFFORT"], ["ASSUMED", "BEST_EFFORT"],
    ["BEST_EFFORT", "APPROXIMATED", "ASSUMED", "BEST_EFFORT"],
])
def test_best_effort_preserves_precedence_and_all_reasons(codes: list[DiagnosticCode]) -> None:
    tags = {"BEST_EFFORT": "WEIGHTED", "ASSUMED": "DERIVED", "APPROXIMATED": "TIMEPIN"}
    diags = [
        Diagnostic(code, "warning", "d.json", "query", "reason", "check", remark=tags[code])
        for code in codes
    ]
    assert remark_outcome(diags) == (
        "best_effort", tuple(dict.fromkeys(tags[code] for code in codes))
    )


def test_best_effort_counts_as_converted_with_visible_summary() -> None:
    items = [
        ItemResult("d.json", "", "dashboard", "D", "best_effort", ("WEIGHTED",)),
        ItemResult("d.json", "widgets[0]", "widget", "W", "best_effort", ("WEIGHTED",)),
    ]
    report = {"items": [i.to_dict() for i in items], "summary": summarize(items, 0)}
    assert report["summary"]["widgets"]["best_effort"] == 1
    assert "1/1 widgets, 1 best_effort [WEIGHTED]" in format_summary(report, {})
    entry = manifest_entry("p", "dashboard", "d.json", "x", "best_effort", ("WEIGHTED",))
    assert (entry["eligible"], entry["remarks"]) == (True, ["WEIGHTED"])
    assert exit_code_for(items) == 2


def test_best_effort_manifest_is_selected_for_publish(tmp_path: Path) -> None:
    source = tmp_path / "d.json"
    source.write_text(json.dumps({
        "title": "Coverage", "layout_type": "ordered", "widgets": [{
            "definition": {"type": "timeseries", "requests": [
                {"q": "avg:postgresql.connections{*}"}
            ]}
        }]
    }))
    out = tmp_path / "out"
    run_convert(ConvertOptions("datadog", [str(source)], ["mappings/datadog"], str(out)))
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["entries"]:
        if entry["kind"] == "dashboard":
            entry.update(outcome="best_effort", remarks=["WEIGHTED"])
    manifest_path.write_text(json.dumps(manifest))
    result = run_publish(PublishOptions(str(out), dry_run=True), {
        "DASH0_API_URL": "https://api.example.dash0.com",
        "DASH0_AUTH_TOKEN": "test-token",
        "DASH0_DATASET": "demo",
    })
    assert result.exit_code == 0
    assert len(result.lines) == 1
    assert result.lines[0].endswith("[best_effort: WEIGHTED]")
