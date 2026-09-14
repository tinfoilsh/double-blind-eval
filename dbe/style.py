"""ANSI styling for terminal output, standard library only.

Color is on only when writing to a terminal, and off when NO_COLOR is set or TERM is
"dumb" (https://no-color.org). Every render function takes a `Style` so notebooks, pipes
and tests get plain text without needing to strip escape codes.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap

BOX_MAX_WIDTH = 100
BOX_PADDING = 1

_RESET = "\x1b[0m"
_CODES = {
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "cyan": "\x1b[36m",
}


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, text: str, *names: str) -> str:
        if not self.enabled or not text:
            return text
        return "".join(_CODES[n] for n in names) + text + _RESET

    def bold(self, text: str) -> str:
        return self._wrap(text, "bold")

    def dim(self, text: str) -> str:
        return self._wrap(text, "dim")

    def red(self, text: str) -> str:
        return self._wrap(text, "red")

    def green(self, text: str) -> str:
        return self._wrap(text, "green")

    def yellow(self, text: str) -> str:
        return self._wrap(text, "yellow")

    def cyan(self, text: str) -> str:
        return self._wrap(text, "cyan")

    def heading(self, text: str) -> str:
        return self._wrap(text, "bold", "cyan")

    def check(self, done: bool) -> str:
        """A checklist box: green when done, dim when pending."""
        return self.green("[x]") if done else self.dim("[ ]")

    def box(self, title: str, body: str, width: int | None = None) -> str:
        """`body` wrapped inside a rounded border, with `title` on the top edge.

        Width follows the terminal so the box never wraps mid-line; ANSI codes are applied
        after wrapping so they do not count against the width.
        """
        if width is None:
            width = min(shutil.get_terminal_size((80, 24)).columns, BOX_MAX_WIDTH)
        inner = width - 2 - 2 * BOX_PADDING
        pad = " " * BOX_PADDING
        wrapped = textwrap.wrap(body, inner) or [""]
        top = f"\u256d\u2500 {title} " + "\u2500" * (width - len(title) - 5) + "\u256e"
        rows = [f"\u2502{pad}{line.ljust(inner)}{pad}\u2502" for line in wrapped]
        bottom = "\u2570" + "\u2500" * (width - 2) + "\u256f"
        if not self.enabled:
            return "\n".join([top, *rows, bottom])
        styled_top = self.yellow(f"\u256d\u2500 ") + self.yellow(self.bold(title)) + self.yellow(" " + "\u2500" * (width - len(title) - 5) + "\u256e")
        styled_rows = [f"{self.yellow(chr(0x2502))}{pad}{line.ljust(inner)}{pad}{self.yellow(chr(0x2502))}" for line in wrapped]
        return "\n".join([styled_top, *styled_rows, self.yellow(bottom)])


PLAIN = Style(enabled=False)


def detect(stream=None) -> Style:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return PLAIN
    return Style(enabled=bool(getattr(stream, "isatty", lambda: False)()))
