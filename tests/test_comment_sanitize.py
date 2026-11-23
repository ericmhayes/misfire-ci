"""Model-authored text in the comment is sanitized before rendering (Item 3).

`root_cause`/`suggested_fix` are influenced by hostile log/diff content, so the
renderer neutralizes HTML, links/images, marker spoofing, and runaway length.
"""

from __future__ import annotations

from misfire.comment import MARKER, render_comment
from misfire.schema import FailureCategory, Verdict


def _verdict(**over) -> Verdict:
    base = dict(
        root_cause="ok",
        category=FailureCategory.TEST_ASSERTION,
        suggested_fix=None,
        confidence=0.8,
    )
    base.update(over)
    return Verdict(**base)


def test_strips_raw_html() -> None:
    out = render_comment(_verdict(root_cause="<img src=x onerror=alert(1)> broke it"))
    # The model's raw tag never survives — only the escaped form does. (The header
    # carries a trusted brand <img>, so assert on the model's specific payload.)
    assert "<img src=x onerror=" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out


def test_neutralizes_links_and_images() -> None:
    out = render_comment(
        _verdict(
            root_cause="see [click me](http://evil.example)",
            suggested_fix="![pwn](http://evil.example/x.png)",
        )
    )
    # Brackets are escaped so Markdown renders them as inert text, not a link.
    assert "[click me](http://evil.example)" not in out
    assert "\\[click me\\]" in out
    assert "\\[pwn\\]" in out


def test_cannot_spoof_the_upsert_marker() -> None:
    # A hostile root_cause embedding the hidden marker must not inject a second
    # real marker (which could hijack which comment gets updated).
    out = render_comment(_verdict(root_cause=f"nice try {MARKER}"))
    assert out.count(MARKER) == 1  # only the real one the renderer prepends
    assert "&lt;!-- misfire:advisory --&gt;" in out


def test_collapses_newlines_to_one_line() -> None:
    out = render_comment(_verdict(root_cause="line one\n### Fake heading\nline two"))
    # Collapsed to one line, so `###` is mid-line and cannot render as a heading.
    assert "\n### Fake heading" not in out
    assert "line one ### Fake heading line two" in out


def test_caps_length() -> None:
    out = render_comment(_verdict(root_cause="A" * 5000))
    assert "…" in out
    assert "A" * 5000 not in out


def test_normal_text_is_readable() -> None:
    out = render_comment(_verdict(root_cause="auth.py:42 changed 'active' to 'pending'"))
    assert "auth.py:42 changed 'active' to 'pending'" in out


def test_defangs_urls_in_model_text() -> None:
    out = render_comment(_verdict(root_cause="fix it at https://evil.example/login"))
    assert "https://evil.example" not in out  # can't autolink a live phishing URL
    assert "hxxps://evil.example" in out
