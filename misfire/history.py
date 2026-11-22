"""The flaky-history seam.

`get_test_history` needs to know how often a given test has failed on *recent,
unrelated* commits — the signal the flaky-vs-real classifier in `flaky.py` turns
into a verdict. That data comes from a provider behind the swappable
`TestHistoryProvider` interface:

  - Today, `GitHubTestHistoryProvider` reads it on demand from the GitHub REST
    API — no persistent store, no cold-start, nothing to operate.
  - Another provider could drop in behind the same interface without
    touching the tools, prompt, or classifier.

Keeping it behind an interface costs nothing today beyond the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TestHistory:
    """Recent pass/fail record for a single test — the flaky-vs-real input.

    The load-bearing field is `failures_on_other_commits`: a test that fails
    across commits *other* than the one under test is failing independently of
    this PR's change, which is the core signal that it's flaky rather than a
    real regression this PR introduced.
    """

    test_id: str
    total_runs: int  # recent runs in which this test was observed
    failures: int  # of those, how many it failed
    # Failures observed on head SHAs other than the commit under investigation.
    failures_on_other_commits: int
    # Times the test failed on one attempt and passed on a re-run of the SAME
    # commit (fail→pass retry). A same-commit flip is near-proof of flakiness —
    # the strongest signal we have, since the code didn't change between attempts.
    retried_and_passed: int = 0

    @property
    def failure_rate(self) -> float:
        """Fraction of observed runs in which the test failed (0.0 if unseen)."""
        return self.failures / self.total_runs if self.total_runs else 0.0

    @property
    def has_signal(self) -> bool:
        """Whether there's enough history to say anything (avoids cold-start guesses)."""
        return self.total_runs > 0

    @classmethod
    def empty(cls, test_id: str) -> "TestHistory":
        """No history found — the classifier should treat this as 'unknown', not 'real'."""
        return cls(test_id=test_id, total_runs=0, failures=0, failures_on_other_commits=0)


@runtime_checkable
class TestHistoryProvider(Protocol):
    """The swappable flaky-history seam.

    Every provider exposes exactly this
    method, so `flaky.py`, `tools.py`, and the adapters never learn where the
    history actually comes from.
    """

    def get_history(self, test_id: str, *, current_sha: str | None = None) -> TestHistory:
        """Return recent pass/fail history for `test_id`.

        `current_sha` is the commit under investigation; the provider excludes it
        when tallying `failures_on_other_commits` so the flaky signal reflects
        *unrelated* runs only.
        """
        ...
