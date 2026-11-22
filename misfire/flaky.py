"""Real-vs-flaky classification.

This is deliberately **plain-Python stats over test history, not an LLM call**
. The credibility of the whole feature rests on the flaky verdict being
a deterministic, reproducible number the model cannot fabricate or be talked out
of. The agent may *read* this verdict (it's surfaced through `get_test_history`)
and explain it, but it does not get to invent it.

The signal, in one sentence: a test that fails on commits *other* than the one
under review is failing independently of this PR — so it's likely flaky rather
than a regression this change introduced.
"""

from __future__ import annotations

from .history import TestHistory
from .schema import FlakyAssessment, FlakyClassification

# A single failure on an unrelated commit is enough to *lean* flaky; confidence
# then scales with how often it fails elsewhere and how much history we have.
_FLAKY_OTHER_FAILURE_MIN = 1
# Below this many observed runs we discount confidence — a tiny sample can't
# strongly support either call.
_MIN_RUNS_FOR_CONFIDENCE = 5
# Confidence gained per clean unrelated run when calling a failure REAL.
_REAL_CONFIDENCE_PER_RUN = 0.05
_CONFIDENCE_FLOOR = 0.5
_CONFIDENCE_CEIL = 0.95


def _bounded(value: float) -> float:
    return round(max(_CONFIDENCE_FLOOR, min(value, _CONFIDENCE_CEIL)), 2)


def classify(history: TestHistory) -> FlakyAssessment:
    """Classify a failing test as real vs. likely flaky from its recent history.

    - No history → UNKNOWN (never guess REAL on absence of data; that would let a
      genuinely flaky test be blamed on the PR).
    - Fails on other commits → LIKELY_FLAKY, confidence rising with that rate.
    - Fails only on the commit under test → REAL, confidence rising with the
      number of clean unrelated runs backing it up.

    Confidence is additionally scaled down when the observed sample is small.
    """
    # Strongest signal first: a fail→pass retry on the SAME commit is near-proof
    # of flakiness — the code didn't change between attempts, so a deterministic
    # regression couldn't have flipped to green. This outranks the cross-commit
    # signal and even a thin sample.
    if history.retried_and_passed >= 1:
        return FlakyAssessment(
            classification=FlakyClassification.LIKELY_FLAKY,
            confidence=0.9,
            rationale=(
                f"Failed then passed on a re-run of the same commit "
                f"({history.retried_and_passed}×) — the failure isn't "
                "deterministic, a hallmark of a flaky test."
            ),
        )

    if not history.has_signal:
        return FlakyAssessment(
            classification=FlakyClassification.UNKNOWN,
            confidence=0.2,
            rationale="No recent history for this test; cannot judge flakiness.",
        )

    sample_factor = min(history.total_runs / _MIN_RUNS_FOR_CONFIDENCE, 1.0)

    if history.failures_on_other_commits >= _FLAKY_OTHER_FAILURE_MIN:
        other_rate = history.failures_on_other_commits / history.total_runs
        confidence = _bounded((_CONFIDENCE_FLOOR + other_rate) * sample_factor)
        return FlakyAssessment(
            classification=FlakyClassification.LIKELY_FLAKY,
            confidence=confidence,
            rationale=(
                f"Failed on {history.failures_on_other_commits} of {history.total_runs} recent "
                "runs built from other commits, so it fails independently of this PR."
            ),
        )

    confidence = _bounded(
        (_CONFIDENCE_FLOOR + _REAL_CONFIDENCE_PER_RUN * history.total_runs) * sample_factor
    )
    return FlakyAssessment(
        classification=FlakyClassification.REAL,
        confidence=confidence,
        rationale=(
            f"Passed on every recent unrelated run ({history.total_runs} observed) and failed "
            "only on the commit under test — the failure appears specific to this change."
        ),
    )
