"""Bounded sliding-window attempt log, used by the login throttle."""

import math
from collections import OrderedDict
from collections.abc import Callable, Hashable


class SlidingWindowLog[K: Hashable]:
    """
    Attempt timestamps per key within a sliding window, bounded in size.

    Keys are kept ordered by their latest timestamp, oldest first. The clock
    is monotonic, so the first key is normally the next to expire, and
    dropping expired keys only ever looks at the front: amortized O(1) per
    call, no matter how many keys are tracked.

    ``withdraw_at`` can remove a key's newest timestamp and leave it further
    back than its new latest timestamp says. That is safe: ``recent`` drops
    such a key on access once it has expired, and otherwise it lingers only
    until the keys ahead of it expire, at most by the gap between its old and
    new latest timestamp. Meanwhile it still counts toward capacity, so
    capacity waits and counts can only err on the long side, never short.

    Timestamps are short lists rather than deques: a deque costs about 1 KB
    even when nearly empty, which matters with hundreds of thousands of keys.
    """

    def __init__(
        self,
        window_seconds: float,
        *,
        max_keys: int,
        max_per_key: int,
        clock: Callable[[], float],
        on_add: Callable[[K], None] | None = None,
        on_remove: Callable[[K], None] | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.max_per_key = max_per_key
        self._clock = clock
        self._on_add = on_add
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
        if timestamps[-1] <= cutoff:
            # Not reachable while the expiry order holds (the front sweep above
            # would have dropped it), but never index past the end if it does not.
            del self._entries[key]
            self._removed(key)
            return None
        expired = 0
        while timestamps[expired] <= cutoff:
            expired += 1
        del timestamps[:expired]
        return timestamps

    def active_count(self) -> int:
        """Number of keys, after dropping the ones that have expired."""
        self._drop_expired_keys(self._clock())
        return len(self._entries)

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

    def add(self, key: K, at: float | None = None) -> bool:
        """
        Record an attempt; False if the log is full of active keys.

        ``at`` is the timestamp to record (default: now). Pass the value from
        one clock read when the caller must later withdraw exactly this
        attempt; it must not be earlier than the key's existing timestamps.
        """
        timestamps = self.recent(key)
        if timestamps is None:
            if self.is_full():
                return False
            timestamps = self._entries[key] = []
            if self._on_add is not None:
                self._on_add(key)
        else:
            self._entries.move_to_end(key)
        timestamps.append(self._clock() if at is None else at)
        if len(timestamps) > self.max_per_key:
            del timestamps[0]
        return True

    def discard(self, key: K) -> None:
        if self._entries.pop(key, None) is not None:
            self._removed(key)

    def withdraw_at(self, key: K, at: float) -> None:
        """
        Remove the attempt recorded at ``at`` for ``key``, e.g. a reservation.

        Removes exactly that timestamp, so other attempts, older or newer,
        keep their own expiry. A no-op if it has already expired or the key
        is gone. Expired timestamps are pruned first.
        """
        # recent() sweeps expired keys and prunes this key's old timestamps;
        # the key may be gone afterwards.
        timestamps = self.recent(key)
        if timestamps is None:
            return
        # Search from the end: a reservation is usually the newest entry.
        for index in range(len(timestamps) - 1, -1, -1):
            if timestamps[index] == at:
                del timestamps[index]
                break
        else:
            return
        if not timestamps:
            del self._entries[key]
            self._removed(key)

    def clear(self) -> None:
        self._entries.clear()

    def seconds_until_expiry(self, timestamp: float) -> int:
        return self._seconds_until(timestamp)

    def _seconds_until(self, timestamp: float) -> int:
        # Rounded to microseconds first: reservation timestamps can be nudged
        # by a few float steps to stay unique (see LoginThrottle.acquire), and
        # that must not add a whole second to a Retry-After.
        remaining = round(timestamp + self.window_seconds - self._clock(), 6)
        return max(1, math.ceil(remaining))
