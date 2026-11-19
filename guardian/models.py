"""Data models for Guardian approval requests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ApprovalRequest:
    """A single intercepted tool invocation awaiting a user decision."""

    request_id: str
    server_name: str
    tool_name: str
    arguments: dict[str, Any]
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def create(
        cls, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> "ApprovalRequest":
        return cls(
            request_id=str(uuid.uuid4()),
            server_name=server_name,
            tool_name=tool_name,
            arguments=dict(arguments),
        )

    @property
    def short_id(self) -> str:
        return self.request_id[:8]
