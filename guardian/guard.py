"""The single choke point between MCP clients and upstream tools.

Every ``tools/call`` goes through ``ToolGateway.call``. The upstream is invoked
only after the broker returns an explicit Allow. Nothing else in the codebase
should call upstream tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .approval import ApprovalBroker
from .audit import summarize_arguments
from .models import ApprovalRequest

log = logging.getLogger("guardian.guard")


class Outcome(str, Enum):
    FORWARDED = "forwarded"            # allowed and upstream answered
    UPSTREAM_ERROR = "upstream_error"  # allowed but upstream failed
    BLOCKED = "blocked"                # user denied, timed out, or Guardian refused
    UNKNOWN_TOOL = "unknown_tool"      # not in the registry; no popup, no upstream call
    INVALID_ARGUMENTS = "invalid_arguments"  # failed schema; no popup, no upstream call


@dataclass
class GuardResult:
    outcome: Outcome
    message: str
    request_id: str | None = None
    upstream_result: Any = None  # upstream CallToolResult, only for FORWARDED
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.outcome is not Outcome.FORWARDED


@dataclass(frozen=True)
class ToolRecord:
    exposed_name: str
    server_name: str
    upstream_name: str
    description: str
    input_schema: dict[str, Any]


class UpstreamCaller(Protocol):
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any: ...


class ToolGateway:
    def __init__(
        self,
        broker: ApprovalBroker,
        upstream: UpstreamCaller,
        tools: dict[str, ToolRecord],
    ) -> None:
        self._broker = broker
        self._upstream = upstream
        self._tools = tools
        self._validators: dict[str, Draft202012Validator] = {}
        for name, record in tools.items():
            try:
                self._validators[name] = Draft202012Validator(record.input_schema or {})
            except SchemaError:
                log.warning("upstream schema invalid for %s; calls will be refused", name)

    @property
    def tools(self) -> dict[str, ToolRecord]:
        return self._tools

    async def call(self, exposed_name: str, arguments: dict[str, Any] | None) -> GuardResult:
        arguments = dict(arguments or {})

        record = self._tools.get(exposed_name)
        if record is None:
            log.warning("refused unknown tool %r", exposed_name)
            return GuardResult(Outcome.UNKNOWN_TOOL, f"Unknown tool: {exposed_name}")

        validator = self._validators.get(exposed_name)
        if validator is None:
            return GuardResult(Outcome.INVALID_ARGUMENTS, "Tool schema unavailable; refusing call")
        try:
            validator.validate(arguments)
        except ValidationError as exc:
            log.info("invalid arguments for %s: %s", exposed_name, exc.message)
            return GuardResult(Outcome.INVALID_ARGUMENTS, f"Invalid arguments: {exc.message}")

        request = ApprovalRequest.create(record.server_name, record.upstream_name, arguments)
        log.info(
            "approval requested id=%s server=%s tool=%s args=%s",
            request.short_id, record.server_name, record.upstream_name,
            summarize_arguments(arguments),
        )

        allowed = await self._broker.request_approval(request)
        if not allowed:
            log.info("BLOCKED id=%s server=%s tool=%s", request.short_id,
                     record.server_name, record.upstream_name)
            return GuardResult(
                Outcome.BLOCKED,
                "Guardian blocked this tool call. The user did not approve it "
                "(denied, timed out, or Guardian could not obtain approval). "
                "The upstream tool was not called.",
                request_id=request.request_id,
            )

        # First point at which the side effect may happen.
        log.info("ALLOWED id=%s; forwarding to upstream", request.short_id)
        try:
            upstream_result = await self._upstream.call_tool(
                record.server_name, record.upstream_name, arguments
            )
        except Exception as exc:  # upstream failure must not crash Guardian
            log.exception("upstream call failed id=%s", request.short_id)
            return GuardResult(
                Outcome.UPSTREAM_ERROR,
                f"Approved, but the upstream server failed: {type(exc).__name__}",
                request_id=request.request_id,
            )

        log.info("OUTCOME id=%s forwarded", request.short_id)
        return GuardResult(
            Outcome.FORWARDED,
            "forwarded",
            request_id=request.request_id,
            upstream_result=upstream_result,
        )
