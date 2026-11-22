"""Unit tests for the pure cost-discipline helpers (no network, no LLM)."""

from __future__ import annotations

from misfire.log_utils import truncate_logs


def test_truncate_returns_short_logs_unchanged() -> None:
    logs = "line 1\nline 2\nline 3"
    assert truncate_logs(logs, tail_lines=200) == logs


def test_truncate_keeps_the_failing_tail() -> None:
    logs = "\n".join(f"line {i}" for i in range(1000))
    out = truncate_logs(logs, tail_lines=10)
    assert "line 999" in out          # tail preserved
    assert "line 500" not in out      # bulk dropped
    assert "truncated" in out         # elision marker present


def test_truncate_preserves_first_error_block_from_head() -> None:
    # The originating error sits near the top; the tail is unrelated noise.
    head = ["setup ok"] * 50
    head[10] = "E   AssertionError: boom in auth.py:42"
    tail = [f"teardown {i}" for i in range(20)]
    logs = "\n".join(head + tail)

    out = truncate_logs(logs, tail_lines=20)
    assert "E   AssertionError: boom in auth.py:42" in out  # error rescued from head
    assert "teardown 19" in out                             # tail still present
    # Head noise before the error is dropped.
    assert out.count("setup ok") < 50


def test_truncate_tail_only_when_no_error_in_head() -> None:
    logs = "\n".join(f"noise {i}" for i in range(500))
    out = truncate_logs(logs, tail_lines=5)
    lines = out.splitlines()
    assert lines[0].startswith("...")   # leading truncation marker
    assert lines[-1] == "noise 499"


def test_clamps_a_pathologically_long_single_line() -> None:
    # One 10 MB minified line in an otherwise short log must not pass through
    # whole — line-based trimming alone wouldn't catch it.
    logs = "ok\n" + ("x" * 10_000_000) + "\ndone"
    out = truncate_logs(logs, tail_lines=200)
    assert len(out) < 50_000
    assert "line truncated" in out
    assert "done" in out  # the real tail survives


def test_caps_total_output_size() -> None:
    # Many medium lines, each under the per-line cap, still can't exceed the
    # overall byte ceiling.
    logs = "\n".join("y" * 1000 for _ in range(500))  # ~500 KB, 500 lines
    out = truncate_logs(logs, tail_lines=500)
    assert len(out) <= 100_000 + 100  # total cap (plus the short notice prefix)
    assert "output capped" in out
