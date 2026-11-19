import asyncio
import threading
from typing import Any

from guardian.approval import ApprovalBroker
from guardian.guard import Outcome, ToolGateway, ToolRecord

SCHEMA = {
    "type": "object",
    "properties": {"to": {"type": "string"}},
    "required": ["to"],
    "additionalProperties": False,
}


class FakeUpstream:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail = fail

    async def call_tool(self, server_name, tool_name, arguments):
        self.calls.append((server_name, tool_name, arguments))
        if self.fail:
            raise ConnectionError("upstream gone")
        return {"content": "ok", "isError": False}


def _tools():
    return {
        "gmail__create_draft": ToolRecord(
            exposed_name="gmail__create_draft",
            server_name="gmail",
            upstream_name="create_draft",
            description="",
            input_schema=SCHEMA,
        )
    }


def _auto_decider(broker: ApprovalBroker, allowed: bool, delay: float = 0.01):
    """Stands in for the UI: decides shortly after the popup would appear."""
    def notify(request, _expires_at):
        threading.Timer(delay, broker.decide, args=(request.request_id, allowed)).start()
    return notify


def _run(gateway_factory, args):
    async def scenario():
        gateway = gateway_factory()
        return await gateway.call("gmail__create_draft", args)
    return asyncio.run(scenario())


def test_blocked_call_never_reaches_upstream():
    upstream = FakeUpstream()
    broker = ApprovalBroker(timeout_seconds=5, on_request=None)

    def factory():
        broker._on_request = _auto_decider(broker, allowed=False)
        return ToolGateway(broker, upstream, _tools())

    result = _run(factory, {"to": "a@example.com"})
    assert result.outcome is Outcome.BLOCKED
    assert result.is_error
    assert upstream.calls == []


def test_allowed_call_is_forwarded_exactly_once():
    upstream = FakeUpstream()
    broker = ApprovalBroker(timeout_seconds=5)

    def factory():
        broker._on_request = _auto_decider(broker, allowed=True)
        return ToolGateway(broker, upstream, _tools())

    result = _run(factory, {"to": "a@example.com"})
    assert result.outcome is Outcome.FORWARDED
    assert upstream.calls == [("gmail", "create_draft", {"to": "a@example.com"})]


def test_timeout_blocks_without_upstream_call():
    upstream = FakeUpstream()
    broker = ApprovalBroker(timeout_seconds=0.05)  # nobody decides
    result = _run(lambda: ToolGateway(broker, upstream, _tools()), {"to": "x"})
    assert result.outcome is Outcome.BLOCKED
    assert upstream.calls == []


def test_unknown_tool_and_invalid_arguments_never_prompt_or_call():
    upstream = FakeUpstream()
    prompts: list[str] = []
    broker = ApprovalBroker(timeout_seconds=5, on_request=lambda r, e: prompts.append(r.tool_name))

    async def scenario():
        gw = ToolGateway(broker, upstream, _tools())
        unknown = await gw.call("gmail__delete_everything", {})
        invalid = await gw.call("gmail__create_draft", {"to": 42})
        extra = await gw.call("gmail__create_draft", {"to": "x", "bcc": "y"})
        return unknown.outcome, invalid.outcome, extra.outcome

    outcomes = asyncio.run(scenario())
    assert outcomes == (Outcome.UNKNOWN_TOOL, Outcome.INVALID_ARGUMENTS, Outcome.INVALID_ARGUMENTS)
    assert prompts == []
    assert upstream.calls == []


def test_broken_notifier_fails_closed():
    upstream = FakeUpstream()

    def boom(_request, _expires_at):
        raise RuntimeError("UI exploded")

    broker = ApprovalBroker(timeout_seconds=5, on_request=boom)
    result = _run(lambda: ToolGateway(broker, upstream, _tools()), {"to": "x"})
    assert result.outcome is Outcome.BLOCKED
    assert upstream.calls == []


def test_upstream_failure_after_allow_is_a_controlled_error():
    upstream = FakeUpstream(fail=True)
    broker = ApprovalBroker(timeout_seconds=5)

    def factory():
        broker._on_request = _auto_decider(broker, allowed=True)
        return ToolGateway(broker, upstream, _tools())

    result = _run(factory, {"to": "x"})
    assert result.outcome is Outcome.UPSTREAM_ERROR
    assert result.is_error
    assert len(upstream.calls) == 1
