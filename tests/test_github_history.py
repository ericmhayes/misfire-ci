"""Failure-marker detection must span the common test runners, not just pytest.

If a framework's failure line isn't recognized, the test looks like it "passed
on every unrelated run" and a flaky failure gets mislabeled REAL — the worst
direction to be wrong in. These lock in detection across pytest/Go/Jest/TAP.
"""

from __future__ import annotations

import pytest

from misfire.github_client import RunRef
from misfire.github_history import GitHubTestHistoryProvider, _test_failed_in_logs
from misfire.schema import FlakyClassification

FAILING_LINES = [
    (
        "pytest FAILED summary",
        "FAILED tests/test_auth.py::test_status - AssertionError",
        "tests/test_auth.py::test_status",
    ),
    ("pytest section header", "_______________ test_status ________________", "test_status"),
    ("go test", "--- FAIL: TestStatus (0.00s)", "TestStatus"),
    ("jest glyph", "  ✕ renders the status badge (12 ms)", "renders the status badge"),
    ("tap not ok", "not ok 4 - status is active", "status is active"),
    ("generic error", "ERROR in payment.spec: status mismatch", "payment.spec"),
]


@pytest.mark.parametrize("name,line,test_id", FAILING_LINES, ids=[f[0] for f in FAILING_LINES])
def test_detects_failure_across_frameworks(name, line, test_id) -> None:
    logs = f"some setup output\n{line}\nmore output\n"
    assert _test_failed_in_logs(logs, test_id) is True


def test_passing_run_is_not_a_failure() -> None:
    logs = "PASS tests/test_auth.py::test_status\n✓ renders the status badge (3 ms)\nok 4 - fine\n"
    assert _test_failed_in_logs(logs, "test_status") is False
    assert _test_failed_in_logs(logs, "renders the status badge") is False


def test_marker_must_share_the_line_with_the_test_id() -> None:
    # A failure elsewhere in the log doesn't implicate an unrelated test id.
    logs = "FAILED tests/test_other.py::test_x\n... test_status ran fine ...\n"
    assert _test_failed_in_logs(logs, "test_status") is False


# --- pass-on-retry detection (the strongest flaky signal) -------------------

TID = "tests/test_pay.py::test_charge"


class _FakeClient:
    """Minimal GitHubClient stand-in for the history provider."""

    def __init__(self, current, runs, run_logs, attempt_logs) -> None:
        self._current = current
        self._runs = runs
        self._run_logs = run_logs  # run_id -> latest-attempt logs
        self._attempt_logs = attempt_logs  # (run_id, attempt) -> logs

    def get_run(self, run_id):
        return self._current

    def list_workflow_runs(self, workflow_id, *, limit=20):
        return self._runs

    def _download_run_logs(self, run_id):
        return self._run_logs.get(run_id, "")

    def download_run_attempt_logs(self, run_id, attempt):
        if (run_id, attempt) not in self._attempt_logs:
            raise RuntimeError("attempt logs expired")
        return self._attempt_logs[(run_id, attempt)]


def _ref(run_id, *, conclusion, branch, attempt=1, sha="s") -> RunRef:
    return RunRef(
        run_id=run_id,
        workflow_id=900,
        head_sha=sha,
        conclusion=conclusion,
        head_branch=branch,
        run_attempt=attempt,
    )


def _provider(runs, run_logs, attempt_logs) -> GitHubTestHistoryProvider:
    current = _ref(100, conclusion="failure", branch="feature")
    client = _FakeClient(current, runs, run_logs, attempt_logs)
    return GitHubTestHistoryProvider(client, run_id=100)


def test_detects_fail_then_pass_on_same_commit_retry() -> None:
    # Run 50 succeeded on attempt 2, but the test FAILED on attempt 1 → flip.
    retry = _ref(50, conclusion="success", branch="main", attempt=2)
    provider = _provider(
        runs=[retry],
        run_logs={50: f"{TID} passed\n"},
        attempt_logs={(50, 1): f"FAILED {TID} - AssertionError\n"},
    )
    h = provider.get_history(TID)
    assert h.retried_and_passed == 1
    assert h.total_runs == 1
    assert h.failures == 0
    from misfire.flaky import classify

    assert classify(h).classification is FlakyClassification.LIKELY_FLAKY


def test_no_flip_when_prior_attempt_did_not_fail_the_test() -> None:
    retry = _ref(50, conclusion="success", branch="main", attempt=2)
    provider = _provider(
        runs=[retry],
        run_logs={50: "all good\n"},  # latest attempt doesn't mention the test
        attempt_logs={(50, 1): "some unrelated failure\n"},
    )
    h = provider.get_history(TID)
    assert h.retried_and_passed == 0
    assert h.total_runs == 0  # test never observed → no signal


def test_first_attempt_success_is_not_a_retry_flip() -> None:
    # A clean single-attempt success is not evidence of flakiness.
    clean = _ref(50, conclusion="success", branch="main", attempt=1)
    provider = _provider(runs=[clean], run_logs={50: f"{TID} passed\n"}, attempt_logs={})
    h = provider.get_history(TID)
    assert h.retried_and_passed == 0


def test_unavailable_prior_attempt_degrades_quietly() -> None:
    # Attempt logs expired → no crash, just no retry signal.
    retry = _ref(50, conclusion="success", branch="main", attempt=2)
    provider = _provider(runs=[retry], run_logs={50: f"{TID} passed\n"}, attempt_logs={})
    h = provider.get_history(TID)  # download raises → caught → None → no flip
    assert h.retried_and_passed == 0
