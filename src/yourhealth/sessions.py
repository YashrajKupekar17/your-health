"""Conversation storage behind a small interface.

`InMemorySessionStore` is what the demo runs. It is single-process by design: a production
deployment implements the same three methods on Redis or Postgres (serialising Session + messages),
so the server can run several workers and survive restarts.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Protocol

from .agent import Agent


class SessionStore(Protocol):
    def put(self, agent: Agent) -> str: ...
    def get(self, session_id: str) -> Agent | None: ...
    def __len__(self) -> int: ...


class InMemorySessionStore:
    """LRU + idle-TTL store. Thread-safe. Expired or evicted sessions simply return None."""

    def __init__(self, max_sessions: int, idle_ttl_s: float, clock: Callable[[], float] = time.monotonic):
        self._items: OrderedDict[str, tuple[Agent, float]] = OrderedDict()
        self._max, self._ttl, self._clock = max_sessions, idle_ttl_s, clock
        self._lock = threading.Lock()

    def put(self, agent: Agent) -> str:
        sid = agent.session.id
        with self._lock:
            self._items[sid] = (agent, self._clock())
            self._items.move_to_end(sid)
            self._evict()
        return sid

    def get(self, session_id: str) -> Agent | None:
        with self._lock:
            self._evict()
            item = self._items.get(session_id)
            if item is None:
                return None
            self._items[session_id] = (item[0], self._clock())  # touch
            self._items.move_to_end(session_id)
            return item[0]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def _evict(self) -> None:
        now = self._clock()
        for sid in [sid for sid, (_, seen) in self._items.items() if now - seen > self._ttl]:
            del self._items[sid]
        while len(self._items) > self._max:
            self._items.popitem(last=False)
