"""Tiny publish/subscribe bus for Server-Sent Events.

Publishers may run in worker threads (sync FastAPI endpoints); each subscriber
is an asyncio queue tied to its own event loop.
"""
from __future__ import annotations

import asyncio
import json
import threading


class EventBus:
    def __init__(self, max_queue: int = 1000):
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self._max = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(self._max)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, x) for (l, x) in self._subs if x is not q]

    def publish(self, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            def put(q=q):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # a slow browser tab loses events; the table reloads from the API
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                pass  # that loop is closed

    @staticmethod
    def format(payload: dict) -> str:
        return f"event: {payload['event']}\ndata: {json.dumps(payload['data'], default=str)}\n\n"
