"""Bounded sliding-window attempt log, used by the login throttle."""

import math
from collections import OrderedDict
from collections.abc import Callable, Hashable


class SlidingWindowLog[K: Hashable]:
    """
    Attempt timestamps per key within a sliding window, bounded in size.

    Keys are kept ordered by their latest timestamp, oldest first. The clock
    is monotonic, so the first key is always the next to expire, and dropping
    expired keys only ever looks at the front: amortized O(1) per call, no
    matter how many keys are tracked.

    Timestamps are short lists rather than deques: a deque costs about 1 KB
    even when nearly empty, which matters with a million keys.
    """

    def __init__(
        self,
        window_seconds: float,
        *,
        max_keys: int,
        max_per_key: int,
        clock: Callable[[], float],
        on_remove: Callable[[K], None] | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.max_per_key = max_per_key
        self._clock = clock
        self._on_remove = on_remove
        self._entries: OrderedDict[K, list[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: K) -> bool:
        return key in self._entries

    def _removed(self, key: K) -> None:
        if self._on_remove is not None:
            self._on_remove(key)

    def _drop_expired_keys(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._entries:
            key, oldest = next(iter(self._entries.items()))
            if oldest[-1] > cutoff:
                return
            self._entries.popitem(last=False)
            self._removed(key)

    def recent(self, key: K) -> list[float] | None:
        """Timestamps for ``key`` inside the window, oldest first, or None."""
        now = self._clock()
        self._drop_expired_keys(now)
        timestamps = self._entries.get(key)
        if timestamps is None:
            return None
        cutoff = now - self.window_seconds
        # The latest timestamp is inside the window, so this never empties it.
        expired = 0
        while timestamps[expired] <= cutoff:
            expired += 1
        del timestamps[:expired]
        return timestamps

    def is_full(self) -> bool:
        return len(self._entries) >= self.max_keys

    def seconds_until_capacity(self, key: K) -> int | None:
        """
        None if ``key`` can be recorded, else seconds until a slot frees up.

        Only a new key can be refused, and only when every slot holds a key
        that is still active.
        """
        if self.recent(key) is not None or not self.is_full():
            return None
        oldest = next(iter(self._entries.values()))
        return self._seconds_until(oldest[-1])

    def add(self, key: K) -> bool:
        """Record an attempt now; False if the log is full of active keys."""
        timestamps = self.recent(key)
        if timestamps is None:
            if self.is_full():
                return False
            timestamps = self._entries[key] = []
        else:
            self._entries.move_to_end(key)
        timestamps.append(self._clock())
        if len(timestamps) > self.max_per_key:
            del timestamps[0]
        return True

    def discard(self, key: K) -> None:
        if self._entries.pop(key, None) is not None:
            self._removed(key)

    def remove_latest(self, key: K) -> None:
        """
        Withdraw the newest timestamp for ``key``.

        The key keeps its place in the expiry order even though its latest
        timestamp may now be older. That only delays dropping it; the counts
        from ``recent`` stay exact.
        """
        timestamps = self._entries.get(key)
        if timestamps is None:
            return
        timestamps.pop()
        if not timestamps:
            del self._entries[key]
            self._removed(key)

    def clear(self) -> None:
        self._entries.clear()

    def seconds_until_expiry(self, timestamp: float) -> int:
        return self._seconds_until(timestamp)

    def _seconds_until(self, timestamp: float) -> int:
        return max(1, math.ceil(timestamp + self.window_seconds - self._clock()))
