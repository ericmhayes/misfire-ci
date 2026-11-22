"""Flaky-history provider: reads per-test history from the GitHub API.

Implements the `TestHistoryProvider` seam (see `history.py`) with no persistent
store — it samples a bounded window of recent runs of the same workflow and
scans each run's logs for the test id. Another provider could replace it behind
the same interface later; today there is nothing to operate.

Cost note: each `get_history` call may download up to `window` run logs. Logs are
cached per run on the instance so classifying several tests in one investigation
reuses the same downloads; the window is kept small.
"""

from __future__ import annotations

import re

from .github_client import GitHubClient
from .history import TestHistory

# A test is considered *failed* in a run when its id appears on a line that also
# carries a failure marker. The markers span the common test runners so the
# flaky signal isn't pytest-only — a missed marker makes a flaky failure look
# like it "passed on every unrelated run" and get mislabeled REAL, the worst
# direction to be wrong in.
_FAIL_MARKER = re.compile(
    r"\bFAIL(ED|URE)?\b"  # pytest `FAILED`, generic FAIL/FAILURE, Jest `FAIL path`
    r"|\bERROR\b"
    r"|AssertionError"
    r"|---\s*FAIL"  # Go: `--- FAIL: TestName`
    r"|\bnot ok\b"  # TAP (node-tap, prove)
    r"|[✕✗×]"  # Jest / Mocha / Vitest failing glyphs
    r"|^_+ .* _+$",  # pytest `____ test_name ____` section header
    re.IGNORECASE,
)


def _test_failed_in_logs(logs: str, test_id: str) -> bool:
    """Heuristic: did `test_id` fail in these logs?"""
    for line in logs.splitlines():
        if test_id in line and _FAIL_MARKER.search(line):
            return True
    return False


class GitHubTestHistoryProvider:
    """`TestHistoryProvider` backed by on-demand GitHub REST reads.

    Constructed with the GitHub client and the failing run under investigation;
    it resolves that run's workflow and head SHA lazily, then tallies how often
    `test_id` failed across the recent window — and how often it failed on
    commits *other* than the one under test (the flaky signal).
    """

    def __init__(self, client: GitHubClient, run_id: int, *, window: int = 20) -> None:
        self._client = client
        self._run_id = run_id
        self._window = window
        self._current = None  # resolved RunRef for the failing run (lazy)
        self._logs: dict[int, str] = {}  # run_id -> raw logs (per-instance cache)
        self._attempts: dict[tuple[int, int], str | None] = {}  # (run_id, attempt) -> logs

    def _run_logs(self, run_id: int) -> str:
        if run_id not in self._logs:
            self._logs[run_id] = self._client._download_run_logs(run_id)
        return self._logs[run_id]

    def _attempt_logs(self, run_id: int, attempt: int) -> str | None:
        """Logs for a specific earlier attempt, or None if unavailable.

        An old attempt's logs can be expired/rate-limited; the retry signal is a
        bonus, so a fetch failure degrades silently rather than breaking history.
        """
        key = (run_id, attempt)
        if key not in self._attempts:
            try:
                self._attempts[key] = self._client.download_run_attempt_logs(run_id, attempt)
            except Exception:
                self._attempts[key] = None
        return self._attempts[key]

    def _is_retry_flip(self, run, test_id: str) -> bool:
        """True if `test_id` failed in a prior attempt but the run then succeeded.

        A re-run of the *same commit* that flips red→green means the failure was
        non-deterministic. Only checked for runs that actually needed a re-run
        (cheap: these are rare), and only against the immediately prior attempt.
        """
        if run.conclusion != "success" or run.run_attempt <= 1:
            return False
        prior = self._attempt_logs(run.run_id, run.run_attempt - 1)
        return prior is not None and _test_failed_in_logs(prior, test_id)

    def get_history(self, test_id: str, *, current_sha: str | None = None) -> TestHistory:
        if self._current is None:
            self._current = self._client.get_run(self._run_id)
        current = self._current

        runs = self._client.list_workflow_runs(current.workflow_id, limit=self._window)
        if not runs:
            return TestHistory.empty(test_id)

        total = 0
        failures = 0
        failures_on_other = 0
        retried_and_passed = 0
        for run in runs:
            logs = self._run_logs(run.run_id)
            # A re-run of the same commit that flipped red→green is strong flaky
            # evidence even if the latest (green) attempt no longer mentions the
            # test — detect it from the prior failed attempt.
            if self._is_retry_flip(run, test_id):
                total += 1
                retried_and_passed += 1
                continue
            # Only count runs where this test actually ran — a run where the test
            # id never appears (e.g. before the test existed, or it was skipped)
            # is not evidence either way, and counting it would inflate the "it
            # passed cleanly elsewhere" signal for new/renamed tests.
            if test_id not in logs:
                continue
            total += 1
            if _test_failed_in_logs(logs, test_id):
                failures += 1
                # The load-bearing flaky signal: a failure on a branch *other*
                # than the PR under test fails independently of this PR. Comparing
                # branches (not SHAs) is what excludes the PR's *own* earlier
                # pushes — those share the head branch and are not "unrelated".
                if run.head_branch != current.head_branch:
                    failures_on_other += 1

        return TestHistory(
            test_id=test_id,
            total_runs=total,
            failures=failures,
            failures_on_other_commits=failures_on_other,
            retried_and_passed=retried_and_passed,
        )
