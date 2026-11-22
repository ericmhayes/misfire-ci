"""Structured output types."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FailureCategory(str, Enum):
    """Coarse bucket for the failure, chosen by the agent."""

    TEST_ASSERTION = "test_assertion"
    COMPILE_ERROR = "compile_error"
    DEPENDENCY = "dependency"
    TIMEOUT = "timeout"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class FlakyClassification(str, Enum):
    """Real-vs-flaky call for a failing test.

    Derived deterministically from test history by `flaky.classify` — never by
    the model: the whole point is a stats layer the LLM can't fabricate.
    """

    REAL = "real"  # failure is specific to the change under test
    LIKELY_FLAKY = "likely_flaky"  # test also fails on unrelated commits
    UNKNOWN = "unknown"  # not enough history to judge


class FlakyAssessment(BaseModel):
    """The deterministic flaky-vs-real verdict attached to a diagnosis."""

    classification: FlakyClassification
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in the classification.")
    rationale: str = Field(..., description="Plain-English why, grounded in the history stats.")


class Evidence(BaseModel):
    """Deterministic, Misfire-authored receipts shown under the advisory.

    None of this is model-authored — it's the raw inputs behind the verdict (a
    link to the failing run, the history counts the flaky call was computed from)
    so a reader can verify the claim rather than take it on faith.
    """

    run_url: Optional[str] = Field(None, description="Link to the failing workflow run.")
    sampled_runs: Optional[int] = Field(None, description="Recent runs the test was observed in.")
    failures: Optional[int] = Field(None, description="Of those, how many failed.")
    failures_on_other: Optional[int] = Field(None, description="Failures on other branches.")
    retried_and_passed: Optional[int] = Field(None, description="Same-commit fail→pass retries.")


class Verdict(BaseModel):
    """The agent's diagnosis of a failing build.

    This is what the Claude adapter returns and what `comment.render_comment`
    turns into a PR comment body.
    """

    root_cause: str = Field(..., description="One-paragraph plain-English root cause.")
    category: FailureCategory = Field(..., description="Coarse failure bucket.")
    suggested_fix: Optional[str] = Field(
        None, description="Concrete next step, if the agent is confident enough to offer one."
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Agent confidence in the diagnosis.")
    flaky: Optional[FlakyAssessment] = Field(
        None,
        description="Real-vs-flaky call from test history; None when no test was implicated.",
    )
