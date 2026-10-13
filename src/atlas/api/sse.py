"""In-process bridge from synchronous worker threads to async SSE generators.

Package AR (docs/125 §3.1; ADR T36): ``run_graph`` stays synchronous and runs on
a daemon thread; the streaming endpoints are ``async def`` and consume frames
through this bridge. All bridge state is touched only on the event-loop thread —
workers hand frames over with ``loop.call_soon_threadsafe``
(:meth:`SSEBridge.offer_threadsafe`), so the bridge itself needs no locks.

Bounded buffering with a terminal slot (docs/125 §2.2): middle frames are kept
in a fixed-size deque and the oldest frame is dropped when full; a terminal frame
always gets through (clearing middle frames when the deque is full) and is
delivered exactly once. After :meth:`SSEBridge.close` (client disconnect) every
offer becomes a cheap no-op; the worker thread and ``run_graph`` are NOT
cancelled — disconnect is not cancellation (docs/24 §3, docs/80 §3.5).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

#: Max middle (non-terminal) frames buffered per stream. Frame volume is
#: O(nodes); 1024 is far above normal graphs and is only hit with pathological
#: graphs or dead-slow consumers.
SSE_BRIDGE_MAXSIZE = 1024

#: Interval between SSE comment heartbeats (``: keepalive``) when idle.
SSE_HEARTBEAT_SECONDS = 15

#: Log one warning per N dropped middle frames.
_SSE_DROP_WARN_EVERY = 100

#: Frame keys that terminate a stream. Exactly one terminal frame is delivered.
TERMINAL_KEYS = frozenset(
    {"__result__", "__stopped__", "__superseded__", "__cancelled__", "__error__"}
)

_CLOSED = object()


class BridgeClosed(Exception):
    """Raised by :meth:`SSEBridge.get` once the bridge is closed/drained.

    Normal generators break on the terminal frame before this can happen; the
    exception mainly serves tests and defensive generator teardown.
    """


def is_terminal(event: dict[str, Any]) -> bool:
    return any(key in event for key in TERMINAL_KEYS)


class SSEBridge:
    """Bounded one-way bridge for one SSE stream (one worker, one consumer)."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        maxsize: int = SSE_BRIDGE_MAXSIZE,
    ) -> None:
        self._loop = loop
        self._maxsize = maxsize
        self._frames: deque[dict[str, Any]] = deque()
        # None while open with no terminal; terminal event dict once offered;
        # _CLOSED after close() without a terminal.
        self._terminal: Any = None
        self._terminal_taken = False
        self._wakeup = asyncio.Event()
        self._closed = False
        self.dropped = 0

    # -- worker side -------------------------------------------------------

    def offer_threadsafe(self, event: dict[str, Any]) -> None:
        """Schedule :meth:`offer` on the event-loop thread (worker entry)."""
        self._loop.call_soon_threadsafe(self.offer, event)

    def offer(self, event: dict[str, Any]) -> None:
        """Offer a frame. Runs on the loop thread (via call_soon_threadsafe)."""
        if self._closed or self._terminal is not None:
            return
        if is_terminal(event):
            # Terminal must always get through: if the deque is full, drop all
            # buffered middle frames so the terminal is not delayed behind a
            # dead-slow consumer.
            if len(self._frames) >= self._maxsize:
                cleared = len(self._frames)
                self._frames.clear()
                self.dropped += cleared
                logger.warning(
                    "SSE bridge dropped %d buffered frames to place terminal frame",
                    cleared,
                )
            self._terminal = event
        else:
            if len(self._frames) >= self._maxsize:
                self._frames.popleft()
                self.dropped += 1
                if self.dropped % _SSE_DROP_WARN_EVERY == 0:
                    logger.warning(
                        "SSE bridge has dropped %d middle frames (consumer too slow)",
                        self.dropped,
                    )
            self._frames.append(event)
        self._wakeup.set()

    # -- generator side ----------------------------------------------------

    def close(self) -> None:
        """Mark the bridge closed (generator ``finally`` on disconnect)."""
        if self._closed:
            return
        self._closed = True
        if self._terminal is None:
            self._terminal = _CLOSED
        self._wakeup.set()

    async def get(self) -> dict[str, Any]:
        """Wait for and return the next frame (middle frames first, terminal last)."""
        if not self._frames and self._terminal is None:
            # Single loop thread: no callback can interleave before this await.
            self._wakeup.clear()
            await self._wakeup.wait()
        if self._frames:
            return self._frames.popleft()
        if self._terminal is _CLOSED or self._terminal_taken:
            raise BridgeClosed
        self._terminal_taken = True
        return self._terminal  # type: ignore[return-value]

    @property
    def has_pending(self) -> bool:
        """True while middle frames or a terminal frame are buffered."""
        return bool(self._frames) or self._terminal is not None

    @property
    def depth(self) -> int:
        """Number of buffered middle frames (test/diagnostics only)."""
        return len(self._frames)
