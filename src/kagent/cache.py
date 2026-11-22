"""Small, thread-safe TTL/LRU caches local to one graph and its configuration."""

import copy
import re
import time
from collections import OrderedDict
from threading import Lock
from typing import Any


def normalize(question: str) -> str:
    """Normalize case, punctuation, and whitespace for final-answer cache keys."""
    return " ".join(re.sub(r"[^\w\s]", "", question.lower()).split())


class TTLCache:
    """An injected clock makes expiry deterministic in tests; values are copied."""

    def __init__(self, ttl: float = 900, maximum: int = 1000, clock: Any = time.monotonic) -> None:
        self.ttl, self.maximum, self.clock = ttl, maximum, clock
        self.values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.lock = Lock()

    def get(self, key: str) -> Any:
        """Return a copy of a live value and refresh its LRU position."""
        with self.lock:
            item = self.values.get(key)
            if item is None:
                return None
            if self.clock() - item[0] >= self.ttl:
                del self.values[key]
                return None
            self.values.move_to_end(key)
            return copy.deepcopy(item[1])

    def put(self, key: str, value: Any) -> None:
        """Store a copy, evicting the least recently used entry at capacity."""
        with self.lock:
            self.values[key] = (self.clock(), copy.deepcopy(value))
            self.values.move_to_end(key)
            while len(self.values) > self.maximum:
                self.values.popitem(last=False)

    def clear(self) -> None:
        """Discard all retained entries."""
        with self.lock:
            self.values.clear()
