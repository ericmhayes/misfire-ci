"""Shared, provider-agnostic tools.

These `@tool`-decorated functions are the tool boundary every provider adapter
invokes. The adapters only translate them into their provider's tool-call format;
they never redefine the tools. Keep all provider assumptions OUT of this file.

`get_test_history` powers the real-vs-flaky feature; its data comes from the
swappable `TestHistoryProvider` seam, bound alongside the GitHub client.
"""

from __future__ import annotations

from typing import Optional

from . import flaky
from .comment import MARKER, render_comment
from .github_client import GitHubClient
from .history import TestHistory, TestHistoryProvider
from .schema import Evidence, FailureCategory, FlakyAssessment, FlakyClassification, Verdict
from .scrub import scrub_secrets


def tool(fn):
    """Marker decorator for the agent-facing tools. It's a pass-through — the
    `@tool` label documents the tool boundary; the registry builds each
    provider's schema from the decorated function's signature + docstring."""
    return fn

# The adapter sets this once per run before driving the agent loop. Kept module
# -level so tool signatures stay simple (the model only supplies domain args).
_client: GitHubClient | None = None
_history: TestHistoryProvider | None = None


def bind(client: GitHubClient) -> None:
    """Bind the GitHub client the tools should use for this run."""
    global _client
    _client = client


def bind_history(provider: TestHistoryProvider) -> None:
    """Bind the flaky-history provider (swappable seam) for this run."""
    global _history
    _history = provider


def _require_client() -> GitHubClient:
    if _client is None:
        raise RuntimeError("tools.bind(client) must be called before tools are used.")
    return _client


def _require_history() -> TestHistoryProvider:
    if _history is None:
        raise RuntimeError("tools.bind_history(provider) must be called before tools are used.")
    return _history


def _format_history(h: TestHistory) -> str:
    """Compact, model-facing summary of a test's recent history + flaky verdict.

    The verdict line is the deterministic `flaky.classify` output — the
    model sees the stats-derived call it must not contradict, not a number it
    invents.
    """
    verdict = flaky.classify(h)
    if not h.has_signal:
        return (
            f"No recent history found for {h.test_id}. "
            f"Classification: {verdict.classification.value} ({verdict.confidence:.0%})."
        )
    retry_note = (
        f" Failed-then-passed on a same-commit re-run {h.retried_and_passed}×."
        if h.retried_and_passed
        else ""
    )
    return (
        f"{h.test_id}: failed {h.failures}/{h.total_runs} recent runs "
        f"({h.failure_rate:.0%}); {h.failures_on_other_commits} of those failures were on "
        f"commits other than the one under test.{retry_note} "
        f"Classification: {verdict.classification.value} ({verdict.confidence:.0%}) — "
        f"{verdict.rationale}"
    )


@tool
def get_failing_logs(run_id: int) -> str:
    """Fetch the logs of the failing job(s) for a CI run.

    Args:
        run_id: The GitHub Actions workflow run ID that failed.
    """
    return scrub_secrets(_require_client().fetch_failing_logs(run_id))


@tool
def get_pr_diff(pr_number: int) -> str:
    """Fetch the unified diff of the pull request under test.

    Args:
        pr_number: The pull request number.
    """
    return scrub_secrets(_require_client().fetch_pr_diff(pr_number))


@tool
def get_test_history(test_id: str) -> str:
    """Fetch how often a specific test has failed on recent, unrelated runs.

    Use this to judge whether a failing test is a real regression from this PR
    or a pre-existing flaky test: a test that also fails on other commits is
    likely flaky, not caused by the change under review.

    Args:
        test_id: Identifier of the test (e.g. `tests/test_auth.py::test_status`).
    """
    return _format_history(_require_history().get_history(test_id))


@tool
def post_pr_comment(
    root_cause: str,
    category: FailureCategory,
    confidence: float,
    suggested_fix: Optional[str] = None,
    failing_test_id: Optional[str] = None,
) -> str:
    """Post the advisory comment on the PR. This is the only write action.

    You supply the diagnosis as structured fields — you do NOT write the comment
    body or the real-vs-flaky verdict yourself. Misfire renders the comment, and
    the flaky-vs-real call is computed deterministically from the failing test's
    history (not from anything you assert). Returns the rendered comment body.

    Args:
        root_cause: One-paragraph plain-English root cause of the failure.
        category: Failure bucket — one of: test_assertion, compile_error,
            dependency, timeout, infrastructure, unknown.
        confidence: Your confidence in the diagnosis, 0.0-1.0.
        suggested_fix: Concrete next step, only if you are reasonably confident.
        failing_test_id: The implicated test's id (e.g.
            `tests/test_auth.py::test_status`) when a specific test failed; omit
            for non-test failures. Drives the deterministic flaky verdict.
    """
    try:
        bucket = FailureCategory(category)
    except ValueError:
        bucket = FailureCategory.UNKNOWN

    # Clamp/coerce confidence so a stray value from the model can't raise a
    # pydantic ValidationError and crash the run (schema requires 0.0–1.0).
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    history: TestHistory | None = None
    assessment = None
    if failing_test_id:
        try:
            history = _require_history().get_history(failing_test_id)
            assessment = flaky.classify(history)
        except Exception:
            # The history lookup can fail (expired run logs 410, GitHub
            # rate-limit, network). Never block a ready advisory on it: degrade to
            # an explicit UNKNOWN verdict and still post the diagnosis.
            assessment = FlakyAssessment(
                classification=FlakyClassification.UNKNOWN,
                confidence=0.0,
                rationale="Test history was unavailable, so flakiness could not be assessed.",
            )

    verdict = Verdict(
        root_cause=root_cause,
        category=bucket,
        confidence=confidence,
        suggested_fix=suggested_fix,
        flaky=assessment,
    )
    body = render_comment(verdict, _build_evidence(history))
    _require_client().upsert_pr_comment(_PR_NUMBER, body, marker=MARKER)
    return body


def _build_evidence(history: TestHistory | None) -> Evidence | None:
    """Assemble the deterministic receipts shown under the advisory.

    A link to the failing run (when known) plus the raw history counts the flaky
    verdict was computed from — nothing model-authored.
    """
    run_url = None
    if _RUN_ID:
        repo = getattr(_client, "repo", None)
        if repo:
            run_url = f"https://github.com/{repo}/actions/runs/{_RUN_ID}"

    ev = Evidence(run_url=run_url)
    if history is not None and history.has_signal:
        ev.sampled_runs = history.total_runs
        ev.failures = history.failures
        ev.failures_on_other = history.failures_on_other_commits
        ev.retried_and_passed = history.retried_and_passed
    # Nothing to show (no run link and no usable history) → omit the block.
    return ev if (run_url or ev.sampled_runs is not None) else None


# Set alongside bind(); the model is not asked to supply the PR number for the
# write, so the comment can only land on the PR under investigation.
_PR_NUMBER: int = 0
# The failing run under investigation; used to build the evidence run link.
_RUN_ID: int = 0


def set_pr_number(pr_number: int) -> None:
    global _PR_NUMBER
    _PR_NUMBER = pr_number


def set_run_id(run_id: int) -> None:
    global _RUN_ID
    _RUN_ID = run_id


ALL_TOOLS = [get_failing_logs, get_pr_diff, get_test_history, post_pr_comment]
