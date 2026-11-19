"""Upstream MCP client connections (STDIO transport).

Guardian connects to each configured upstream server, records its tools, and
forwards approved calls. Exposed tool names are prefixed with the server name
so collisions between upstreams cannot happen.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .guard import ToolRecord

log = logging.getLogger("guardian.upstream")

NAME_SEPARATOR = "__"


@dataclass(frozen=True)
class UpstreamConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    # If set, only these upstream tool names are exposed. None exposes everything.
    allowed_tools: frozenset[str] | None = None


def exposed_name(server_name: str, tool_name: str) -> str:
    return f"{server_name}{NAME_SEPARATOR}{tool_name}"


class UpstreamManager:
    """Owns upstream sessions. Must be used inside the asyncio loop it was created on."""

    def __init__(self, configs: list[UpstreamConfig]) -> None:
        self._configs = configs
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, ToolRecord] = {}

    async def start(self) -> None:
        for cfg in self._configs:
            try:
                await self._connect(cfg)
            except Exception:
                # A broken upstream must not take Guardian down; its tools stay absent.
                log.exception("failed to connect upstream %r; skipping", cfg.name)

    async def _connect(self, cfg: UpstreamConfig) -> None:
        params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[cfg.name] = session

        count = 0
        cursor = None
        while True:
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                if cfg.allowed_tools is not None and tool.name not in cfg.allowed_tools:
                    continue
                name = exposed_name(cfg.name, tool.name)
                self.tools[name] = ToolRecord(
                    exposed_name=name,
                    server_name=cfg.name,
                    upstream_name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema or {"type": "object"},
                )
                count += 1
            cursor = page.nextCursor
            if not cursor:
                break
        log.info("upstream %r connected with %d tools", cfg.name, count)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"upstream {server_name!r} is not connected")
        return await session.call_tool(tool_name, arguments)

    async def close(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()


class RoutingUpstream:
    """Sends each call to the built-in upstream that owns its server name, else to STDIO."""

    def __init__(self, stdio: UpstreamManager, builtins: dict[str, Any]) -> None:
        self._stdio = stdio
        self._builtins = builtins

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        builtin = self._builtins.get(server_name)
        if builtin is not None:
            return await builtin.call_tool(server_name, tool_name, arguments)
        return await self._stdio.call_tool(server_name, tool_name, arguments)
