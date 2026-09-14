import io

from dbe.checklist import render_checklist
from dbe.style import PLAIN, Style, detect

STATUS = {
    "phase": "collecting",
    "benchmark": {"sha256": "b" * 64, "count": 10},
    "adapter": None,
    "manifest_sha256": "a" * 64,
    "approvals": {"benchmark-owner": True},
    "run": None,
}


def test_disabled_style_is_identity():
    assert (
        PLAIN.bold("x") == "x"
        and PLAIN.check(True) == "[x]"
        and PLAIN.check(False) == "[ ]"
    )


def test_enabled_style_wraps_and_resets():
    s = Style(enabled=True)
    assert s.green("ok") == "\x1b[32mok\x1b[0m"
    assert s.heading("h").startswith("\x1b[1m\x1b[36m") and s.heading("h").endswith(
        "\x1b[0m"
    )
    assert s.bold("") == ""


def test_detect_respects_tty_and_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert not detect(io.StringIO()).enabled

    class Tty(io.StringIO):
        def isatty(self):
            return True

    assert detect(Tty()).enabled
    monkeypatch.setenv("NO_COLOR", "1")
    assert not detect(Tty()).enabled


def test_checklist_styled_output_matches_plain_once_stripped():
    import re

    styled = render_checklist(STATUS, "enc.example", Style(enabled=True))
    plain = render_checklist(STATUS, "enc.example")
    assert "\x1b[" in styled and "\x1b[" not in plain
    assert re.sub(r"\x1b\[[0-9;]*m", "", styled) == plain
