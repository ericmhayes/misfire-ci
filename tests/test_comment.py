"""Unit tests for `comment.render_comment`, including the flaky line."""

from __future__ import annotations

from misfire.comment import render_comment
from misfire.schema import (
    Evidence,
    FailureCategory,
    FlakyAssessment,
    FlakyClassification,
    Verdict,
)


def _verdict(**over) -> Verdict:
    base = dict(
        root_cause="Assertion changed 'active' -> 'pending'.",
        category=FailureCategory.TEST_ASSERTION,
        suggested_fix=None,
        confidence=0.8,
    )
    base.update(over)
    return Verdict(**base)


def test_renders_core_fields_and_footer() -> None:
    out = render_comment(_verdict())
    assert "**Root cause:**" in out
    assert "`test_assertion`" in out
    assert "**Confidence:** 80%" in out
    assert "advisory only" in out  # footer


def test_header_shows_the_brand_icon() -> None:
    out = render_comment(_verdict())
    assert "misfire-icon-light-64.png" in out  # logo in the header
    assert "Misfire — build failure analysis" in out


def test_omits_flaky_line_when_no_test_implicated() -> None:
    out = render_comment(_verdict(flaky=None))
    assert "**Verdict:**" not in out


def test_renders_flaky_verdict_line() -> None:
    out = render_comment(
        _verdict(
            flaky=FlakyAssessment(
                classification=FlakyClassification.LIKELY_FLAKY,
                confidence=0.62,
                rationale="Failed on 5 of 20 recent runs built from other commits.",
            )
        )
    )
    assert "**Verdict:** 🟡 Likely flaky (62%)" in out
    assert "other commits" in out


def test_renders_real_verdict_line() -> None:
    out = render_comment(
        _verdict(
            flaky=FlakyAssessment(
                classification=FlakyClassification.REAL,
                confidence=0.9,
                rationale="Passed on every recent unrelated run.",
            )
        )
    )
    assert "🔴 Likely a real failure (90%)" in out


def test_no_evidence_block_without_evidence() -> None:
    assert "Evidence" not in render_comment(_verdict())


def test_renders_evidence_run_link_and_history() -> None:
    out = render_comment(
        _verdict(),
        Evidence(
            run_url="https://github.com/o/r/actions/runs/99",
            sampled_runs=20,
            failures=8,
            failures_on_other=7,
            retried_and_passed=1,
        ),
    )
    assert "<details><summary>Evidence</summary>" in out
    assert "Failing run: https://github.com/o/r/actions/runs/99" in out
    assert "sampled 20 recent run(s) — 8 failed, 7 on other branches, 1× passed on retry." in out


def test_evidence_run_link_only_when_no_history() -> None:
    out = render_comment(_verdict(), Evidence(run_url="https://github.com/o/r/actions/runs/5"))
    assert "Failing run:" in out
    assert "History:" not in out
