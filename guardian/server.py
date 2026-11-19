"""MCP server facade: exposes the approved upstream tools to the MCP host.

Every tools/call is routed through ToolGateway.call(), which enforces approval
before the upstream is touched. This module does no policy decisions itself.
"""

from __future__ import annotations

import logging
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server

from .guard import Outcome, ToolGateway

log = logging.getLogger("guardian.server")

SERVER_NAME = "gmail-guardian"


def build_server(gateway: ToolGateway) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=rec.exposed_name,
                description=rec.description or None,
                inputSchema=rec.input_schema,
            )
            for rec in gateway.tools.values()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        result = await gateway.call(name, arguments)

        if result.outcome is Outcome.FORWARDED and result.upstream_result is not None:
            # Preserve upstream content and isError exactly as returned.
            return result.upstream_result

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.message)],
            isError=True,
        )

    return server
