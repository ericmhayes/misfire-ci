"""Cost-discipline helpers.

Never feed whole logs to the model: take the failing tail plus the stack
frames that matter.
"""

from __future__ import annotations

import re

# Rough token budget for the log slice handed to the agent. Tune with count_tokens.
_DEFAULT_TAIL_LINES = 200
# How many lines to keep from the first error block found in the dropped head.
_HEAD_BLOCK_LINES = 20
# A single line is capped at this many characters: line-based trimming alone
# can't stop one pathological line (a 10 MB minified bundle or single-line JSON
# stream) from blowing the context in one tool call.
_MAX_LINE_CHARS = 2000
# Hard ceiling on the whole returned slice, independent of line count — the last
# resort byte cap so the model context can't be flooded.
_MAX_TOTAL_CHARS = 100_000
# Lines that signal the originating failure (pytest 'E ', tracebacks, CI markers).
_ERROR_RE = re.compile(
    r"traceback|exception|error[:\s]|assert|\bFAIL(ED|URE)?\b|^E\s", re.IGNORECASE
)


def _clamp_line(line: str) -> str:
    if len(line) <= _MAX_LINE_CHARS:
        return line
    dropped = len(line) - _MAX_LINE_CHARS
    return f"{line[:_MAX_LINE_CHARS]}… [line truncated, {dropped} chars dropped]"


def _cap_total(text: str) -> str:
    if len(text) <= _MAX_TOTAL_CHARS:
        return text
    kept = text[-_MAX_TOTAL_CHARS:]
    return f"... [output capped to the last {_MAX_TOTAL_CHARS} characters] ...\n{kept}"


def truncate_logs(logs: str, tail_lines: int = _DEFAULT_TAIL_LINES) -> str:
    """Reduce a raw CI log to the portion that carries the failure signal.

    The failure and its stack usually sit at the *end* of a CI log, so we keep
    the last `tail_lines` lines. When the log is longer than that, we also scan
    the dropped head for the first error/traceback line and prepend a short block
    starting there — otherwise a very long log would bury the originating error
    above the tail window. Over-long individual lines are clamped and the whole
    slice is capped to a byte ceiling. Short, well-behaved logs are returned
    unchanged.

    This could later widen (multiple stack frames, per-step slicing).
    """
    raw = logs.splitlines()
    lines = [_clamp_line(ln) for ln in raw]
    if len(lines) <= tail_lines:
        if lines == raw and len(logs) <= _MAX_TOTAL_CHARS:
            return logs  # unchanged fast path, preserves exact bytes
        return _cap_total("\n".join(lines))

    tail = lines[-tail_lines:]
    head = lines[:-tail_lines]  # the part we would otherwise drop

    first_err = next((i for i, ln in enumerate(head) if _ERROR_RE.search(ln)), None)
    if first_err is None:
        return _cap_total("\n".join([f"... [{len(head)} earlier lines truncated] ...", *tail]))

    head_block = head[first_err : first_err + _HEAD_BLOCK_LINES]
    dropped_before = first_err
    dropped_between = len(head) - first_err - len(head_block)
    out = []
    if dropped_before:
        out.append(f"... [{dropped_before} earlier lines truncated] ...")
    out.extend(head_block)
    if dropped_between:
        out.append(f"... [{dropped_between} lines truncated] ...")
    out.extend(tail)
    return _cap_total("\n".join(out))
