"""Fail a test when integration code blocks Home Assistant's event loop.

Why this exists. Home Assistant ships its own detector
(``homeassistant.block_async_io``), but it deliberately skips ``open``,
``Path.read_text``, ``Path.open``, ``os.listdir``... whenever ``unittest`` is
loaded (``skip_for_tests=True``): under any test runner the calls that matter
most are never checked. A file read added to a code path that runs on the
event loop therefore passes every test and only shows up in production as
``Detected blocking call to read_text ... inside the event loop``.

What it does. ``EventLoopGuard`` wraps the blocking primitives listed in
``GUARDED_CALLS``. A wrapped call is a violation when all of these hold:

* the calling thread is running an asyncio event loop (executor threads and
  the serial worker never are);
* a frame of the integration (``custom_components/enocean_custom``) is on the
  stack, so Home Assistant's own start-up I/O is not blamed on us;
* the call was not made on behalf of ``logging``/``traceback``/``linecache``
  (formatting a traceback reads source files; production logs through a
  queue handler on another thread);
* when a test drove the call directly, the integration function the test
  called is one that declares itself loop-only: a coroutine, or a function
  named ``async_*`` / ``_async_*``. A test that calls a plain synchronous
  helper from inside ``async def`` (``EnOceanDongle(...)``, which production
  runs in the executor) is the test's own choice and is not reported.

A violation is recorded on the guard *and* raised as ``AssertionError``.
Callers must also assert that ``guard.violations`` is empty, because broad
``except Exception`` handlers on the way up can swallow the raise.
"""

from __future__ import annotations

import asyncio
import builtins
import glob
import inspect
import io
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import serial

REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = str(REPO_ROOT / "custom_components" / "enocean_custom") + os.sep
TEST_DIRS = (str(REPO_ROOT / "tests") + os.sep, str(REPO_ROOT / "scripts") + os.sep)
THIS_FILE = str(Path(__file__).resolve())

# (owner, attribute, label). ``io.open`` is listed next to ``builtins.open``
# because ``pathlib`` calls it through the ``io`` module.
GUARDED_CALLS: tuple[tuple[Any, str, str], ...] = (
    (builtins, "open", "open"),
    (io, "open", "open"),
    (Path, "read_text", "Path.read_text"),
    (Path, "read_bytes", "Path.read_bytes"),
    (Path, "write_text", "Path.write_text"),
    (Path, "write_bytes", "Path.write_bytes"),
    (Path, "open", "Path.open"),
    (Path, "exists", "Path.exists"),
    (Path, "is_file", "Path.is_file"),
    (Path, "is_dir", "Path.is_dir"),
    (Path, "iterdir", "Path.iterdir"),
    (os.path, "exists", "os.path.exists"),
    (os.path, "isfile", "os.path.isfile"),
    (os.path, "isdir", "os.path.isdir"),
    (os.path, "samefile", "os.path.samefile"),
    (os, "listdir", "os.listdir"),
    (os, "scandir", "os.scandir"),
    (os, "walk", "os.walk"),
    (glob, "glob", "glob.glob"),
    (glob, "iglob", "glob.iglob"),
    (time, "sleep", "time.sleep"),
    (serial, "serial_for_url", "serial.serial_for_url"),
    (threading.Thread, "join", "Thread.join"),
)

# Frames that make a file read incidental (source lookup for a traceback).
_INCIDENTAL_FILES = (
    os.sep + "linecache.py",
    os.sep + "traceback.py",
    os.sep + "logging" + os.sep + "__init__.py",
    os.sep + "warnings.py",
)
_COROUTINE_FLAGS = (
    inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR | inspect.CO_ITERABLE_COROUTINE
)


@dataclass(frozen=True, slots=True)
class Violation:
    """One blocking call made by the integration on an event loop thread."""

    call: str
    location: str
    entry_point: str

    def __str__(self) -> str:
        """Render the call, where it was made and what reached it."""
        return (
            f"{self.call} at {self.location} (reached from {self.entry_point}) "
            "ran inside the event loop"
        )


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _is_loop_declared(code: Any) -> bool:
    """Return whether a function declares itself loop-only (coroutine/async_*)."""
    return bool(code.co_flags & _COROUTINE_FLAGS) or code.co_name.lstrip(
        "_"
    ).startswith("async_")


def classify_stack(frame: Any) -> tuple[str, str] | None:
    """Return ``(location, entry_point)`` when the stack is a violation.

    ``frame`` is the caller of the blocking primitive. The stack is walked
    outwards; the walk stops at the first test frame, which is the driver.
    """
    innermost: Any = None
    outermost: Any = None
    driven_by_test = False
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename == THIS_FILE:
            frame = frame.f_back
            continue
        if filename.endswith(_INCIDENTAL_FILES):
            return None
        if filename.startswith(TEST_DIRS):
            driven_by_test = True
            break
        if filename.startswith(INTEGRATION_DIR):
            if innermost is None:
                innermost = frame
            outermost = frame
        frame = frame.f_back
    if innermost is None:
        return None
    if driven_by_test and not _is_loop_declared(outermost.f_code):
        return None
    return _describe(innermost), _describe(outermost)


def _describe(frame: Any) -> str:
    code = frame.f_code
    return f"{_relative(code.co_filename)}:{frame.f_lineno} in {code.co_name}"


def _relative(filename: str) -> str:
    try:
        return str(Path(filename).relative_to(REPO_ROOT))
    except ValueError:
        return filename


@dataclass(slots=True)
class EventLoopGuard:
    """Installable set of wrappers around the blocking primitives."""

    violations: list[Violation] = field(default_factory=list)
    _originals: list[tuple[Any, str, Any]] = field(default_factory=list)

    @property
    def installed(self) -> bool:
        """Return whether the wrappers are currently in place."""
        return bool(self._originals)

    def install(self) -> EventLoopGuard:
        """Wrap every guarded primitive (idempotent)."""
        if self._originals:
            return self
        for owner, name, label in GUARDED_CALLS:
            original = getattr(owner, name)
            setattr(owner, name, self._wrap(original, label))
            self._originals.append((owner, name, original))
        return self

    def uninstall(self) -> None:
        """Restore every primitive."""
        while self._originals:
            owner, name, original = self._originals.pop()
            setattr(owner, name, original)

    def _wrap(self, original: Callable[..., Any], label: str) -> Callable[..., Any]:
        guard = self

        def guarded(*args: Any, **kwargs: Any) -> Any:
            if _on_event_loop():
                verdict = classify_stack(sys._getframe(1))
                if verdict is not None:
                    violation = Violation(label, *verdict)
                    guard.violations.append(violation)
                    raise AssertionError(str(violation))
            return original(*args, **kwargs)

        guarded.__name__ = getattr(original, "__name__", label)
        guarded.__wrapped__ = original  # type: ignore[attr-defined]
        return guarded

    def describe(self) -> str:
        """Return every recorded violation, one per line."""
        return "\n".join(str(violation) for violation in self.violations)


@contextmanager
def forbid_blocking_calls_in_event_loop() -> Iterator[EventLoopGuard]:
    """Install a guard for the duration of a ``with`` block."""
    guard = EventLoopGuard().install()
    try:
        yield guard
    finally:
        guard.uninstall()


# One process-wide guard for the stdlib ``unittest`` run. It is installed by
# ``tests/test_event_loop_guard.py`` when discovery imports that module, so the
# whole suite runs under it; ``tests/test_zz_event_loop_verdict.py`` (last in
# discovery order) fails if any violation was recorded but swallowed.
SUITE_GUARD = EventLoopGuard()
