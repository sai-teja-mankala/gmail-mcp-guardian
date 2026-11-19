import asyncio
import threading

from guardian.approval import ApprovalBroker
from guardian.models import ApprovalRequest


def _req(i: int = 0) -> ApprovalRequest:
    return ApprovalRequest.create("gmail", f"tool{i}", {"i": i})


def test_allow_and_block_resolve_only_the_matching_request():
    async def scenario():
        broker = ApprovalBroker(timeout_seconds=5)
        a, b = _req(1), _req(2)
        ta = asyncio.create_task(broker.request_approval(a))
        tb = asyncio.create_task(broker.request_approval(b))
        await asyncio.sleep(0.01)
        assert broker.decide(a.request_id, True)
        assert broker.decide(b.request_id, False)
        return await ta, await tb

    assert asyncio.run(scenario()) == (True, False)


def test_timeout_denies_and_clears_pending():
    async def scenario():
        broker = ApprovalBroker(timeout_seconds=0.05)
        result = await broker.request_approval(_req())
        return result, broker.pending_count()

    assert asyncio.run(scenario()) == (False, 0)


def test_duplicate_and_stale_decisions_are_rejected():
    async def scenario():
        broker = ApprovalBroker(timeout_seconds=5)
        r = _req()
        t = asyncio.create_task(broker.request_approval(r))
        await asyncio.sleep(0.01)
        first = broker.decide(r.request_id, True)
        second = broker.decide(r.request_id, False)  # duplicate
        result = await t
        late = broker.decide(r.request_id, True)     # stale, already completed
        unknown = broker.decide("not-a-real-id", True)
        return first, second, result, late, unknown

    assert asyncio.run(scenario()) == (True, False, True, False, False)


def test_queue_bound_refuses_extra_requests():
    async def scenario():
        broker = ApprovalBroker(timeout_seconds=5, max_pending=1)
        held = asyncio.create_task(broker.request_approval(_req(1)))
        await asyncio.sleep(0.01)
        refused = await broker.request_approval(_req(2))
        broker.deny_all()
        await held
        return refused

    assert asyncio.run(scenario()) is False


def test_deny_all_resolves_every_pending_request_as_denied():
    async def scenario():
        broker = ApprovalBroker(timeout_seconds=5)
        tasks = [asyncio.create_task(broker.request_approval(_req(i))) for i in range(5)]
        await asyncio.sleep(0.01)
        broker.deny_all()
        return await asyncio.gather(*tasks)

    assert asyncio.run(scenario()) == [False] * 5


def test_decisions_from_another_thread_are_correlated():
    async def scenario():
        requests: list[ApprovalRequest] = []
        ready = threading.Event()

        def notifier(request, _expires_at):
            requests.append(request)
            if len(requests) == 5:
                ready.set()

        broker = ApprovalBroker(timeout_seconds=5, on_request=notifier)
        tasks = [asyncio.create_task(broker.request_approval(_req(i))) for i in range(5)]
        while not ready.is_set():
            await asyncio.sleep(0.005)

        # Simulate the Qt thread deciding in an arbitrary order.
        allow = {requests[0].request_id, requests[3].request_id}

        def ui_thread():
            for r in requests:
                broker.decide(r.request_id, r.request_id in allow)

        t = threading.Thread(target=ui_thread)
        t.start()
        results = await asyncio.gather(*tasks)
        t.join()
        return [r.request_id in allow for r in requests], results

    expected, results = asyncio.run(scenario())
    assert results == expected
