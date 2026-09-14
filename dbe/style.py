"""ANSI styling for terminal output, standard library only.

Colour is on only when writing to a terminal, and off when NO_COLOR is set or TERM is
"dumb" (https://no-color.org). Every render function takes a `Style` so notebooks, pipes
and tests get plain text without needing to strip escape codes.
"""

from __future__ import annotations

import os
import sys

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


PLAIN = Style(enabled=False)


def detect(stream=None) -> Style:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return PLAIN
    return Style(enabled=bool(getattr(stream, "isatty", lambda: False)()))
