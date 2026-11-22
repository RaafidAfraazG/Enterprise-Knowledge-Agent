"""Bounded backoff for Azure rate limits only."""

import time
from collections.abc import Callable
from typing import TypeVar

from openai import RateLimitError

T = TypeVar("T")


def rate_limit_retry(operation: Callable[[], T]) -> T:
    """Try at most three times; other failures propagate immediately."""
    for attempt in range(3):
        try:
            return operation()
        except RateLimitError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise AssertionError("Unreachable")
