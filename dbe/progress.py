"""Small terminal progress helpers, standard library only.

Everything renders to stderr so stdout stays clean for JSON output. When stderr is not
a terminal the bar stays quiet and only a one-line summary is printed at the end.
"""

from __future__ import annotations

import sys
import threading
import time

from dbe.style import PLAIN, detect


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.1f} GB"


class ProgressBar:
    def __init__(self, label: str, unit: str = "bytes", width: int = 28, stream=None, force: bool = False):
        self.label = label
        self.unit = unit
        self.width = width
        self.stream = stream or sys.stderr
        self.live = force or bool(getattr(self.stream, "isatty", lambda: False)())
        self.style = detect(self.stream) if self.live else PLAIN
        self.started = time.monotonic()
        self.finished = False
        self._last = None
        self._last_width = 0

    def _fmt(self, value: float) -> str:
        return human_bytes(value) if self.unit == "bytes" else f"{int(value)}"

    def update(self, done: float, total: float) -> None:
        if self.finished:
            return
        total = max(total, 1)
        frac = min(max(done / total, 0.0), 1.0)
        filled = int(round(frac * self.width))
        counts = f"{frac * 100:3.0f}%  {self._fmt(done)}/{self._fmt(total)}"
        line = f"\r{self.label} [{self.style.green('#' * filled)}{self.style.dim('.' * (self.width - filled))}] {self.style.bold(counts[:4])}{counts[4:]}"
        if self.live and line != self._last:
            self.stream.write(line)
            self.stream.flush()
            self._last = line
            self._last_width = len(self.label) + self.width + len(counts) + 4

    def finish(self, message: str | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        elapsed = time.monotonic() - self.started
        summary = message or f"{self.label}: done in {elapsed:.1f}s"
        if self.live:
            self.stream.write("\r" + " " * self._last_width + "\r")
            summary = f"{self.style.green(chr(0x2713))} {summary}"
        self.stream.write(summary + "\n")
        self.stream.flush()


class Spinner:
    """Shows a message with a spinner while a blocking call runs in the main thread."""

    FRAMES = "|/-\\"

    def __init__(self, message: str, stream=None, force: bool = False):
        self.message = message
        self.stream = stream or sys.stderr
        self.live = force or bool(getattr(self.stream, "isatty", lambda: False)())
        self.style = detect(self.stream) if self.live else PLAIN
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started: float | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.started = time.monotonic()
        if not self.live:
            self.stream.write(self.message + "...\n")
            self.stream.flush()
            return
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            self.stream.write(f"\r{self.message} {self.style.cyan(self.FRAMES[i % len(self.FRAMES)])} {self.style.dim(f'{time.monotonic() - self.started:4.0f}s')}")
            self.stream.flush()
            i += 1
            self._stop.wait(0.15)

    def stop(self, message: str | None = None) -> None:
        if self.started is None:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self.stream.write("\r" + " " * (len(self.message) + 12) + "\r")
        elapsed = time.monotonic() - self.started
        summary = message or f"{self.message}: done in {elapsed:.1f}s"
        if self.live:
            summary = f"{self.style.green(chr(0x2713))} {summary}"
        self.stream.write(summary + "\n")
        self.stream.flush()
        self.started = None
        self._thread = None
