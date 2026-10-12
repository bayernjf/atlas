"""Unit tests for the bounded SSE bridge (docs/125 §3.1, U1377/U1378 parts).

These tests exercise the bridge directly with asyncio.run — no FastAPI — per
the repo convention of not depending on a pytest asyncio plugin.
"""

import asyncio
import logging
import threading

import pytest

from atlas.api.sse import (
    SSE_BRIDGE_MAXSIZE,
    BridgeClosed,
    SSEBridge,
)


def _run(coro):
    return asyncio.run(coro)


def test_middle_frames_drain_fifo_then_terminal_once():
    async def drive():
        bridge = SSEBridge(asyncio.get_running_loop(), maxsize=4)
        bridge.offer({"type": "node_start", "seq": 1})
        bridge.offer({"type": "node_end", "seq": 2})
        terminal = {"__result__": {"status": "ok"}}
        bridge.offer(terminal)

        first = await bridge.get()
        second = await bridge.get()
        third = await bridge.get()
        assert first["seq"] == 1
        assert second["seq"] == 2
        assert third is terminal

        # Terminal is delivered exactly once; the bridge is then drained.
        with pytest.raises(BridgeClosed):
            await bridge.get()

    _run(drive())


def test_full_deque_drops_oldest_and_counts():
    async def drive():
        bridge = SSEBridge(asyncio.get_running_loop(), maxsize=4)
        for seq in range(6):
            bridge.offer({"seq": seq})

        assert bridge.dropped == 2
        assert bridge.depth == 4
        frames = [await bridge.get() for _ in range(4)]
        assert [frame["seq"] for frame in frames] == [2, 3, 4, 5]

    _run(drive())


def test_terminal_clears_full_deque_but_is_always_delivered(caplog):
    async def drive():
        bridge = SSEBridge(asyncio.get_running_loop(), maxsize=4)
        for seq in range(4):
            bridge.offer({"seq": seq})

        with caplog.at_level(logging.WARNING, logger="atlas.api.sse"):
            bridge.offer({"__error__": {"message": "boom"}})

        assert bridge.dropped == 4
        assert bridge.depth == 0
        assert any("terminal frame" in record.message for record in caplog.records)
        terminal = await bridge.get()
        assert terminal["__error__"]["message"] == "boom"

        # After a terminal, every further offer is ignored (exactly once).
        bridge.offer({"seq": 99})
        bridge.offer({"__result__": {"status": "late"}})
        assert bridge.depth == 0
        with pytest.raises(BridgeClosed):
            await bridge.get()

    _run(drive())


def test_close_makes_offers_noop_and_get_raises():
    async def drive():
        bridge = SSEBridge(asyncio.get_running_loop(), maxsize=4)
        bridge.offer({"seq": 1})
        bridge.close()

        # Post-close offers are cheap no-ops: no growth, no drop accounting.
        bridge.offer({"seq": 2})
        bridge.offer({"__error__": {"message": "late"}})
        assert bridge.depth == 1
        assert bridge.dropped == 0

        first = await bridge.get()
        assert first["seq"] == 1
        with pytest.raises(BridgeClosed):
            await bridge.get()

    _run(drive())


def test_close_without_terminal_wakes_waiting_consumer():
    async def drive():
        bridge = SSEBridge(asyncio.get_running_loop(), maxsize=4)

        async def closer():
            await asyncio.sleep(0.01)
            bridge.close()

        task = asyncio.create_task(bridge.get())
        await closer()
        with pytest.raises(BridgeClosed):
            await task

    _run(drive())


def test_offer_threadsafe_from_worker_thread_wakes_consumer():
    async def drive():
        loop = asyncio.get_running_loop()
        bridge = SSEBridge(loop, maxsize=4)

        def worker():
            bridge.offer_threadsafe({"seq": 0})
            bridge.offer_threadsafe({"__result__": {"status": "ok"}})

        thread = threading.Thread(target=worker)
        thread.start()
        first = await asyncio.wait_for(bridge.get(), timeout=2)
        terminal = await asyncio.wait_for(bridge.get(), timeout=2)
        thread.join(timeout=2)
        assert first["seq"] == 0
        assert terminal["__result__"]["status"] == "ok"

    _run(drive())


def test_drop_warning_logged_every_hundred(caplog):
    async def drive():
        bridge = SSEBridge(asyncio.get_running_loop())
        assert SSE_BRIDGE_MAXSIZE == 1024
        total = SSE_BRIDGE_MAXSIZE + 100
        with caplog.at_level(logging.WARNING, logger="atlas.api.sse"):
            for seq in range(total):
                bridge.offer({"seq": seq})

        assert bridge.dropped == 100
        warnings = [record for record in caplog.records if "dropped" in record.message]
        assert len(warnings) == 1
        # Oldest 100 frames gone; buffered window starts at seq 100.
        first = await bridge.get()
        assert first["seq"] == 100

    _run(drive())
