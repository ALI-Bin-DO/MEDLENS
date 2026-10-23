"""Bounded, one-at-a-time access to the pipeline for this single-GPU prototype.

One question runs at a time (the GPU, Ollama and the local Qdrant client are shared). At most
`max_waiting` more may wait; anything beyond that is refused immediately as busy instead of
queueing without limit. A reservation is released by the worker thread when its work ends,
so a client that disconnects mid-request cannot free a slot while its work is still running.
"""

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class GateAbandoned(RuntimeError):
    """The reservation was given up before its work started."""


class Reservation:
    """One admitted request. start() in the worker, abandon() if the work never got scheduled."""

    def __init__(self, gate: "InferenceGate") -> None:
        self._gate = gate
        self._lock = threading.Lock()
        self._state = "reserved"  # reserved -> running -> done, or reserved -> done (abandoned)

    def run(self, fn: Callable[..., T], *args: object) -> T:
        """Called in a worker thread: wait for the single run slot, run fn, then release."""
        with self._lock:
            if self._state != "reserved":
                raise GateAbandoned("request was abandoned before it started")
            self._state = "running"
        try:
            with self._gate._running:
                return fn(*args)
        finally:
            self._finish()

    def abandon(self) -> None:
        """Release the reservation if its work never started. Safe to call more than once."""
        with self._lock:
            if self._state == "reserved":
                self._finish_locked()

    def _finish(self) -> None:
        with self._lock:
            if self._state != "done":
                self._finish_locked()

    def _finish_locked(self) -> None:
        self._state = "done"
        self._gate._release()


class InferenceGate:
    def __init__(self, max_waiting: int) -> None:
        if max_waiting < 0:
            raise ValueError("max_waiting must be >= 0")
        self.capacity = 1 + max_waiting  # one running + waiting
        self._running = threading.Lock()
        self._count_lock = threading.Lock()
        self._pending = 0

    @property
    def pending(self) -> int:
        """Requests admitted and not finished (running + waiting)."""
        return self._pending

    def try_reserve(self) -> Reservation | None:
        """Admit a request, or return None if the gate is full (respond busy)."""
        with self._count_lock:
            if self._pending >= self.capacity:
                return None
            self._pending += 1
        return Reservation(self)

    def _release(self) -> None:
        with self._count_lock:
            self._pending -= 1
