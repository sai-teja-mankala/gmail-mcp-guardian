import asyncio
import base64
from email import message_from_bytes
from email.policy import default as default_policy

import pytest

from guardian import gmail


def _decode(raw: str):
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=default_policy)


def test_raw_message_round_trip():
    raw = gmail.build_raw_message("a@example.com", "Hello", "Line one\nLine two")
    msg = _decode(raw)
    assert msg["To"] == "a@example.com"
    assert msg["Subject"] == "Hello"
    assert "Line two" in msg.get_content()


def test_subject_with_newline_is_rejected():
    with pytest.raises(ValueError):
        gmail.build_raw_message("a@example.com", "bad\r\nBcc: x@example.com", "body")


def test_registry_respects_allowed_tools(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    upstream = gmail.GmailUpstream(allowed_tools=frozenset({"create_draft"}))
    assert set(upstream.tools) == {"gmail__create_draft"}
    assert upstream.tools["gmail__create_draft"].upstream_name == "create_draft"


def test_missing_credentials_fail_at_startup(monkeypatch):
    monkeypatch.delenv("GMAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        gmail.GmailUpstream()


def test_unknown_tool_is_refused(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    upstream = gmail.GmailUpstream()
    with pytest.raises(RuntimeError):
        asyncio.run(upstream.call_tool("gmail", "delete_everything", {}))
