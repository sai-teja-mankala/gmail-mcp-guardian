"""Asynchronous approval broker.

Each intercepted tool call gets its own asyncio Future. Only the coroutine that
owns the request waits on it; other requests keep running. Decisions can arrive
from any thread (e.g. the Qt UI thread) and are marshalled onto the broker's
event loop with ``call_soon_threadsafe``.

Fail-closed rules implemented here:
* timeout            -> denied
* unknown request ID -> rejected, nothing resolved
* duplicate decision -> rejected (first decision wins)
* queue full         -> denied without showing anything
* shutdown / close   -> all pending requests denied
* notifier error     -> denied
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .models import ApprovalRequest

log = logging.getLogger("guardian.approval")

# Called on the broker's event-loop thread when a request becomes pending.
# Arguments: the request and its monotonic expiry time (time.monotonic() clock).
RequestNotifier = Callable[[ApprovalRequest, float], None]


@dataclass
class _Pending:
    request: ApprovalRequest
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop
    expires_at: float
    decided: bool = False


def _set_result_if_pending(future: asyncio.Future, allowed: bool) -> None:
    if not future.done():
        future.set_result(allowed)


class ApprovalBroker:
    def __init__(
        self,
        timeout_seconds: float = 90,
        max_pending: int = 30,
        on_request: Optional[RequestNotifier] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_pending = max_pending
        self._on_request = on_request
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # Event-loop side
    # ------------------------------------------------------------------
    async def request_approval(self, request: ApprovalRequest) -> bool:
        """Return True only on an explicit Allow. Anything else is a denial."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        expires_at = time.monotonic() + self.timeout_seconds

        with self._lock:
            if self._closed:
                log.warning("approval refused (broker closed) id=%s", request.short_id)
                return False
            if request.request_id in self._pending:
                log.warning("duplicate request id=%s refused", request.short_id)
                return False
            if len(self._pending) >= self.max_pending:
                log.warning("approval queue full; refused id=%s", request.short_id)
                return False
            self._pending[request.request_id] = _Pending(
                request=request, future=future, loop=loop, expires_at=expires_at
            )

        try:
            if self._on_request is not None:
                self._on_request(request, expires_at)
        except Exception:
            log.exception("approval notifier failed; denying id=%s", request.short_id)
            self._remove(request.request_id)
            return False

        try:
            # Only this coroutine waits here; other requests continue.
            return bool(await asyncio.wait_for(future, timeout=self.timeout_seconds))
        except asyncio.TimeoutError:
            log.info("approval timed out; denied id=%s", request.short_id)
            return False
        finally:
            self._remove(request.request_id)

    # ------------------------------------------------------------------
    # Thread-safe side (callable from the Qt thread)
    # ------------------------------------------------------------------
    def decide(self, request_id: str, allowed: bool) -> bool:
        """Record a decision. Returns False for unknown, stale, or duplicate IDs."""
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.decided:
                return False
            if time.monotonic() >= pending.expires_at:
                return False
            pending.decided = True
            try:
                pending.loop.call_soon_threadsafe(
                    _set_result_if_pending, pending.future, bool(allowed)
                )
            except RuntimeError:  # event loop already closed
                return False
            return True

    def is_actionable(self, request_id: str) -> bool:
        """True if the request is still awaiting a decision and not expired."""
        with self._lock:
            pending = self._pending.get(request_id)
            return (
                pending is not None
                and not pending.decided
                and time.monotonic() < pending.expires_at
            )

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def deny_all(self) -> None:
        """Deny every undecided request. Safe to call from any thread."""
        with self._lock:
            for pending in self._pending.values():
                if pending.decided:
                    continue
                pending.decided = True
                try:
                    pending.loop.call_soon_threadsafe(
                        _set_result_if_pending, pending.future, False
                    )
                except RuntimeError:
                    pass

    def close(self) -> None:
        """Refuse new requests and deny everything still pending."""
        with self._lock:
            self._closed = True
        self.deny_all()

    # ------------------------------------------------------------------
    def _remove(self, request_id: str) -> None:
        with self._lock:
            self._pending.pop(request_id, None)
