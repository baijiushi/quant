"""Shared cancellation helpers for long-running pipeline tasks."""
from __future__ import annotations

import threading


class RunCancelledError(RuntimeError):
    """Raised when a user requests cancellation for the active run."""


def is_cancelled(stop_event: threading.Event | None) -> bool:
    return bool(stop_event and stop_event.is_set())


def raise_if_cancelled(stop_event: threading.Event | None) -> None:
    if is_cancelled(stop_event):
        raise RunCancelledError("任务已被用户终止")
