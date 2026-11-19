"""Built-in Gmail upstream: search threads and create drafts through the Gmail API.

Authorize once with `python -m guardian.gmail_auth`. The token is stored outside
the repository. Tool calls reach this module only after the approval popup
returns Allow, via ToolGateway.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from mcp import types

from .guard import ToolRecord

log = logging.getLogger("guardian.gmail")

SERVER_NAME = "gmail"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
TOKEN_PATH = Path.cwd() / "gmail_token.json"

_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Gmail search query, e.g. 'from:name@example.com' or 'is:unread'.",
        },
        "max_results": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
    },
    "required": ["query"],
    "additionalProperties": False,
}
_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "Recipient email address."},
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "Plain-text body."},
    },
    "required": ["to", "subject", "body"],
    "additionalProperties": False,
}


# ----------------------------------------------------------------------
# OAuth
# ----------------------------------------------------------------------
def client_config() -> dict[str, Any]:
    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in .env")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def save_credentials(creds: Any, token_path: Path = TOKEN_PATH) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def authorize(token_path: Path = TOKEN_PATH) -> None:
    """Interactive, one-time flow. Run from a terminal, not from the MCP host."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(client_config(), SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(creds, token_path)


def load_credentials(token_path: Path = TOKEN_PATH) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        raise RuntimeError(f"No Gmail token at {token_path}. Run: python -m guardian.gmail_auth")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError("Gmail token cannot be refreshed. Run: python -m guardian.gmail_auth")
        creds.refresh(Request())
        save_credentials(creds, token_path)
    return creds


# ----------------------------------------------------------------------
# Message helpers
# ----------------------------------------------------------------------
def build_raw_message(to: str, subject: str, body: str) -> str:
    """Return a base64url-encoded RFC 5322 message for the Gmail API."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject  # EmailMessage rejects header values containing newlines
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


# ----------------------------------------------------------------------
# Upstream
# ----------------------------------------------------------------------
class GmailUpstream:
    """Implements the UpstreamCaller interface for the Gmail tools."""

    server_name = SERVER_NAME

    def __init__(self, allowed_tools: frozenset[str] | None = None,
                 token_path: Path = TOKEN_PATH) -> None:
        client_config()  # fail at startup if credentials are missing
        self._token_path = token_path
        self._service: Any = None
        self._service_lock = threading.Lock()
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "search_mails": self._search_mails,
            "create_draft": self._create_draft,
        }
        specs = [
            ("search_mails", "Search Gmail threads and return metadata and snippets.", _SEARCH_SCHEMA),
            ("create_draft", "Create a Gmail draft. Does not send.", _DRAFT_SCHEMA),
        ]
        self.tools: dict[str, ToolRecord] = {}
        for name, description, schema in specs:
            if allowed_tools is not None and name not in allowed_tools:
                continue
            exposed = f"{SERVER_NAME}__{name}"
            self.tools[exposed] = ToolRecord(
                exposed_name=exposed,
                server_name=SERVER_NAME,
                upstream_name=name,
                description=description,
                input_schema=schema,
            )

    def _get_service(self) -> Any:
        with self._service_lock:
            if self._service is None:
                from googleapiclient.discovery import build

                creds = load_credentials(self._token_path)
                self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            return self._service

    def _search_mails(self, args: dict[str, Any]) -> str:
        svc = self._get_service()
        listing = svc.users().threads().list(
            userId="me", q=args["query"], maxResults=int(args.get("max_results", 10))
        ).execute()
        results = []
        for item in listing.get("threads", []):
            thread = svc.users().threads().get(
                userId="me", id=item["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            messages = thread.get("messages", [])
            if not messages:
                continue
            headers = {h["name"]: h["value"] for h in messages[-1].get("payload", {}).get("headers", [])}
            results.append({
                "thread_id": item["id"],
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": thread.get("snippet", ""),
            })
        return json.dumps({"threads": results}, ensure_ascii=False, indent=2)

    def _create_draft(self, args: dict[str, Any]) -> str:
        svc = self._get_service()
        raw = build_raw_message(args["to"], args["subject"], args["body"])
        draft = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return json.dumps({"draft_id": draft.get("id"), "status": "created"})

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        if server_name != SERVER_NAME:
            raise RuntimeError(f"gmail upstream cannot serve {server_name!r}")
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise RuntimeError(f"unknown gmail tool {tool_name!r}")
        text = await asyncio.to_thread(handler, arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=False,
        )
