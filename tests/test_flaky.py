"""Unit tests for the deterministic real-vs-flaky classifier (`flaky.classify`).

The classifier is pure stats over `TestHistory` — these tests pin its
direction, bounds, and the two canonical calls it must get right.
"""

from __future__ import annotations

from misfire.flaky import classify
from misfire.history import TestHistory
from misfire.schema import FlakyClassification


def _h(total: int, failures: int, other: int) -> TestHistory:
    return TestHistory("t", total_runs=total, failures=failures, failures_on_other_commits=other)


def test_no_history_is_unknown_never_real() -> None:
    v = classify(TestHistory.empty("t"))
    assert v.classification is FlakyClassification.UNKNOWN
    assert v.confidence == 0.2


def test_fails_on_other_commits_is_flaky() -> None:
    # Fails across unrelated commits → not caused by this PR.
    v = classify(_h(total=20, failures=6, other=5))
    assert v.classification is FlakyClassification.LIKELY_FLAKY
    assert 0.5 <= v.confidence <= 0.95
    assert "other commits" in v.rationale


def test_fails_only_on_current_commit_is_real() -> None:
    # Clean on every unrelated run, fails only here → specific to this change.
    v = classify(_h(total=20, failures=1, other=0))
    assert v.classification is FlakyClassification.REAL
    assert 0.5 <= v.confidence <= 0.95
    assert "specific to this change" in v.rationale


def test_flaky_confidence_rises_with_other_commit_failures() -> None:
    low = classify(_h(total=20, failures=2, other=1))
    high = classify(_h(total=20, failures=12, other=10))
    assert high.confidence > low.confidence


def test_real_confidence_rises_with_more_clean_history() -> None:
    small = classify(_h(total=5, failures=1, other=0))
    large = classify(_h(total=20, failures=1, other=0))
    assert large.confidence > small.confidence


def test_small_sample_is_discounted() -> None:
    # Same shape, fewer runs → lower confidence (sample factor).
    few = classify(_h(total=2, failures=1, other=1))
    many = classify(_h(total=10, failures=5, other=5))
    assert few.confidence < many.confidence


def test_confidence_never_exceeds_ceiling() -> None:
    v = classify(_h(total=100, failures=1, other=0))
    assert v.confidence <= 0.95


def _h_retry(total: int, failures: int, other: int, retried: int) -> TestHistory:
    return TestHistory(
        "t",
        total_runs=total,
        failures=failures,
        failures_on_other_commits=other,
        retried_and_passed=retried,
    )


def test_pass_on_retry_is_flaky_with_high_confidence() -> None:
    v = classify(_h_retry(total=1, failures=0, other=0, retried=1))
    assert v.classification is FlakyClassification.LIKELY_FLAKY
    assert v.confidence >= 0.85
    assert "re-run" in v.rationale


def test_pass_on_retry_outranks_a_would_be_real_verdict() -> None:
    # Clean cross-commit history would normally classify REAL; a same-commit
    # fail→pass flip overrides that to LIKELY_FLAKY.
    v = classify(_h_retry(total=20, failures=1, other=0, retried=1))
    assert v.classification is FlakyClassification.LIKELY_FLAKY
