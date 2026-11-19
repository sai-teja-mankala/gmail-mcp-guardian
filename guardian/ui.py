"""PySide6 confirmation popup.

Threading rules (see guide section 8):
* Everything in this module runs on the Qt main thread.
* The asyncio side talks to it only through ``ApprovalBridge`` signals, which
  Qt delivers to the main thread via queued connections.
* Decisions go back through ``broker.decide()``, which is thread-safe and
  marshals onto the asyncio loop. No asyncio Future is touched here.
* One window at a time. Other requests wait in a FIFO deque.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Callable

from PySide6.QtCore import Qt, QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .models import ApprovalRequest

log = logging.getLogger("guardian.ui")


class ApprovalBridge(QObject):
    """Signals emitted from the asyncio thread; received on the Qt thread."""

    request_ready = Signal(object, float)  # (ApprovalRequest, expires_at monotonic)
    shutdown_requested = Signal()


class ApprovalWindow(QDialog):
    def __init__(
        self,
        request: ApprovalRequest,
        expires_at: float,
        on_decision: Callable[[str, bool], None],
    ) -> None:
        super().__init__()
        self._request = request
        self._expires_at = expires_at
        self._on_decision = on_decision
        self._decided = False

        self.setWindowTitle("MCP Guardian - Approve tool call")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>Server:</b> {request.server_name}"))
        layout.addWidget(QLabel(f"<b>Tool:</b> {request.tool_name}"))
        layout.addWidget(QLabel(f"<b>Request ID:</b> {request.short_id}"))
        layout.addWidget(QLabel("<b>Arguments:</b>"))

        args_view = QPlainTextEdit()
        args_view.setReadOnly(True)
        args_view.setPlainText(json.dumps(request.arguments, indent=2, ensure_ascii=False))
        args_view.setMinimumHeight(180)
        layout.addWidget(args_view)

        self._countdown = QLabel()
        layout.addWidget(self._countdown)

        note = QLabel("The tool will NOT run unless you click Allow.")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        block_btn = QPushButton("Block")
        allow_btn = QPushButton("Allow")
        allow_btn.setDefault(False)
        block_btn.setDefault(True)
        block_btn.clicked.connect(lambda: self._finish(False))
        allow_btn.clicked.connect(lambda: self._finish(True))
        buttons.addStretch(1)
        buttons.addWidget(block_btn)
        buttons.addWidget(allow_btn)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)
        self._tick()

    @property
    def request_id(self) -> str:
        return self._request.request_id

    def _tick(self) -> None:
        remaining = max(0, int(self._expires_at - time.monotonic()))
        self._countdown.setText(f"Pending - auto-denied in {remaining}s")
        if remaining <= 0 and not self._decided:
            self._finish(False)

    def _finish(self, allowed: bool) -> None:
        if self._decided:
            return
        self._decided = True
        self._timer.stop()
        self._on_decision(self._request.request_id, allowed)
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # Closing the window without a choice is a Block.
        if not self._decided:
            self._decided = True
            self._timer.stop()
            self._on_decision(self._request.request_id, False)
        super().closeEvent(event)


class ApprovalController(QObject):
    """FIFO presentation of pending requests, one popup at a time."""

    def __init__(
        self,
        decide: Callable[[str, bool], bool],
        is_actionable: Callable[[str], bool],
    ) -> None:
        super().__init__()
        self._decide = decide
        self._is_actionable = is_actionable
        self._queue: deque[tuple[ApprovalRequest, float]] = deque()
        self._window: ApprovalWindow | None = None

    @Slot(object, float)
    def on_request(self, request: ApprovalRequest, expires_at: float) -> None:
        self._queue.append((request, expires_at))
        if self._window is None:
            self._show_next()

    def _on_decision(self, request_id: str, allowed: bool) -> None:
        accepted = self._decide(request_id, allowed)
        log.info("UI decision id=%s allowed=%s accepted=%s",
                 request_id[:8], allowed, accepted)
        self._window = None
        self._show_next()

    def _show_next(self) -> None:
        while self._queue:
            request, expires_at = self._queue.popleft()
            # Skip requests that already timed out or were denied while queued.
            if not self._is_actionable(request.request_id):
                continue
            self._window = ApprovalWindow(request, expires_at, self._on_decision)
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            return

    def deny_everything_visible(self) -> None:
        """Called on shutdown: close the popup as a Block and drop the queue."""
        self._queue.clear()
        if self._window is not None:
            self._window.close()  # closeEvent -> Block
            self._window = None
