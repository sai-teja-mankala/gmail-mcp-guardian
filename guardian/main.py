"""Entry point: python guardian/main.py  (or: python -m guardian.main)

Threading layout (guide sections 8 and 9):
* Main thread   : Qt event loop and the approval popup.
* Worker thread : asyncio loop running the STDIO MCP server and upstream clients.
* Communication : Qt signals (worker -> UI) and broker.decide() (UI -> worker).

STDOUT is reserved for MCP protocol traffic. All diagnostics go to stderr and
the log file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # allow `python guardian/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv 
from mcp.server.stdio import stdio_server
from PySide6.QtWidgets import QApplication

from guardian.approval import ApprovalBroker
from guardian.audit import setup_logging
from guardian.gmail import GmailUpstream
from guardian.guard import ToolGateway
from guardian.server import build_server
from guardian.ui import ApprovalBridge, ApprovalController
from guardian.upstream import RoutingUpstream, UpstreamConfig, UpstreamManager

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "guardian.config.json"

log = logging.getLogger("guardian.main")


@dataclass
class Settings:
    timeout_seconds: float = 90
    max_pending: int = 30
    log_file: Path | None = ROOT / "logs" / "guardian.log"
    upstreams: list[UpstreamConfig] = field(default_factory=list)
    gmail_enabled: bool = False
    gmail_allowed_tools: frozenset[str] | None = None


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env_refs(value: str) -> str:
    """Replace ${NAME} with the environment variable's value.

    Fails loudly if the variable is unset, so a missing secret cannot silently
    produce an upstream with empty credentials.
    """
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(f"config references ${{{name}}} but it is not set (check .env)")
        return os.environ[name]

    return _ENV_REF.sub(repl, value)


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    upstreams = []
    for u in data.get("upstreams", []):
        env = u.get("env")
        allowed = u.get("allowed_tools")
        upstreams.append(
            UpstreamConfig(
                name=u["name"],
                command=expand_env_refs(u["command"]),
                args=[expand_env_refs(a) for a in u.get("args", [])],
                env={k: expand_env_refs(v) for k, v in env.items()} if env else None,
                allowed_tools=frozenset(allowed) if allowed is not None else None,
            )
        )
    gmail = data.get("gmail", {})
    gmail_allowed = gmail.get("allowed_tools")
    log_file = data.get("log_file", "logs/guardian.log")
    return Settings(
        timeout_seconds=float(data.get("timeout_seconds", 90)),
        max_pending=int(data.get("max_pending", 30)),
        log_file=(ROOT / log_file) if log_file else None,
        upstreams=upstreams,
        gmail_enabled=bool(gmail.get("enabled", False)),
        gmail_allowed_tools=frozenset(gmail_allowed) if gmail_allowed is not None else None,
    )


def main() -> int:
    load_dotenv(ROOT / ".env")  # secrets stay out of the config file
    config_path = Path(os.environ.get("GUARDIAN_CONFIG", DEFAULT_CONFIG))
    settings = load_settings(config_path)
    setup_logging(settings.log_file)
    log.info("starting Guardian; config=%s upstreams=%d",
             config_path, len(settings.upstreams))

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # popups come and go; the server stays up

    bridge = ApprovalBridge()
    broker = ApprovalBroker(
        timeout_seconds=settings.timeout_seconds,
        max_pending=settings.max_pending,
        on_request=bridge.request_ready.emit,
    )
    controller = ApprovalController(
        decide=broker.decide,
        is_actionable=broker.is_actionable,
    )
    bridge.request_ready.connect(controller.on_request)
    bridge.shutdown_requested.connect(controller.deny_everything_visible)
    bridge.shutdown_requested.connect(app.quit)

    def worker() -> None:
        try:
            asyncio.run(serve(broker, settings))
        except Exception:
            log.exception("Guardian server crashed; all pending requests denied")
        finally:
            broker.close()
            bridge.shutdown_requested.emit()

    thread = threading.Thread(target=worker, name="guardian-asyncio", daemon=True)
    thread.start()

    code = app.exec()
    broker.close()
    thread.join(timeout=5)
    return code


async def serve(broker: ApprovalBroker, settings: Settings) -> None:
    upstream = UpstreamManager(settings.upstreams)
    try:
        await upstream.start()
        tools = dict(upstream.tools)
        caller: object = upstream
        if settings.gmail_enabled:
            gmail = GmailUpstream(settings.gmail_allowed_tools)
            tools.update(gmail.tools)
            caller = RoutingUpstream(upstream, {gmail.server_name: gmail})
        gateway = ToolGateway(broker, caller, tools)
        server = build_server(gateway)
        log.info("serving %d exposed tools over STDIO", len(tools))
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        broker.close()
        await upstream.close()
        log.info("Guardian server stopped")


if __name__ == "__main__":
    sys.exit(main())
