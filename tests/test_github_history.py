"""Failure-marker detection must span the common test runners, not just pytest.

If a framework's failure line isn't recognized, the test looks like it "passed
on every unrelated run" and a flaky failure gets mislabeled REAL — the worst
direction to be wrong in. These lock in detection across pytest/Go/Jest/TAP.
"""

from __future__ import annotations

import pytest

from misfire.github_history import _test_failed_in_logs

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
