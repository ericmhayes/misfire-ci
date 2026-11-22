"""Unit tests for the structured `post_pr_comment` write action.

Proves the tool — not the model — renders the comment body, and that the
real-vs-flaky verdict in the posted comment is computed deterministically from
history (the caller cannot state it).
"""

from __future__ import annotations

from misfire import tools
from misfire.history import TestHistory


class RecordingClient:
    def __init__(self) -> None:
        self.posts: list[tuple[int, str]] = []

    def upsert_pr_comment(self, pr_number: int, body: str, *, marker: str) -> str:
        self.posts.append((pr_number, body))
        return f"https://github.com/x/y/pull/{pr_number}#issuecomment-1"


class FixedHistory:
    """History provider that returns a preset TestHistory for any test id."""

    def __init__(self, history: TestHistory) -> None:
        self._history = history

    def get_history(self, test_id: str, *, current_sha=None) -> TestHistory:
        return self._history


def _bind(history: TestHistory | None = None) -> RecordingClient:
    client = RecordingClient()
    tools.bind(client)
    tools.set_pr_number(3)
    if history is not None:
        tools.bind_history(FixedHistory(history))
    return client


def test_renders_body_and_posts_it() -> None:
    client = _bind()
    body = tools.post_pr_comment(
        root_cause="Broke the auth assertion.",
        category="test_assertion",
        confidence=0.8,
    )
    assert client.posts[0][0] == 3
    assert client.posts[0][1] == body  # what we return is exactly what we posted
    assert "**Root cause:** Broke the auth assertion." in body
    assert "`test_assertion`" in body
    assert "**Verdict:**" not in body  # no failing_test_id → no flaky line


def test_flaky_verdict_is_computed_from_history_not_the_caller() -> None:
    # History says the test fails on many other commits → deterministically flaky,
    # regardless of what the caller might have wanted to claim.
    client = _bind(TestHistory("t", total_runs=20, failures=8, failures_on_other_commits=7))
    body = tools.post_pr_comment(
        root_cause="test_timeout failed.",
        category="timeout",
        confidence=0.5,
        failing_test_id="tests/test_payments.py::test_timeout",
    )
    assert "Likely flaky" in body
    assert "other commits" in body
    assert client.posts[0][1] == body


def test_real_verdict_from_clean_history() -> None:
    _bind(TestHistory("t", total_runs=20, failures=1, failures_on_other_commits=0))
    body = tools.post_pr_comment(
        root_cause="This PR changed the return value.",
        category="test_assertion",
        confidence=0.9,
        failing_test_id="tests/test_x.py::test_y",
    )
    assert "Likely a real failure" in body


def test_unknown_category_falls_back() -> None:
    _bind()
    body = tools.post_pr_comment(root_cause="huh", category="not_a_category", confidence=0.3)
    assert "`unknown`" in body


def test_out_of_range_confidence_is_clamped() -> None:
    _bind()
    hi = tools.post_pr_comment(root_cause="x", category="test_assertion", confidence=5.0)
    assert "**Confidence:** 100%" in hi
    lo = tools.post_pr_comment(root_cause="x", category="test_assertion", confidence=-2)
    assert "**Confidence:** 0%" in lo


def test_non_numeric_confidence_defaults() -> None:
    _bind()
    body = tools.post_pr_comment(root_cause="x", category="test_assertion", confidence="high")
    assert "**Confidence:** 50%" in body  # coerced to the 0.5 default, no crash


def test_evidence_block_carries_run_link_and_history() -> None:
    client = _bind(TestHistory("t", total_runs=10, failures=2, failures_on_other_commits=1))
    client.repo = "o/r"  # read by _build_evidence to form the run URL
    tools.set_run_id(77)
    try:
        body = tools.post_pr_comment(
            root_cause="x",
            category="test_assertion",
            confidence=0.5,
            failing_test_id="tests/t.py::t",
        )
    finally:
        tools.set_run_id(0)  # don't leak the global into other tests
    assert "actions/runs/77" in body
    assert "sampled 10 recent run(s)" in body
    assert client.posts[0][1] == body
