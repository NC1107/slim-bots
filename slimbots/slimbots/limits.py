"""Per-user cooldowns, rate limits, quotas, and input-bounds checks - each user-facing, never a silent drop."""

from __future__ import annotations

import time
from typing import Any, Callable, Hashable


class ValidationError(ValueError):
    """Raised by a `require_*` bounds check; `str(self)` is the reply to send."""


def require_len(text: str, *, max_len: int, min_len: int = 0, field: str = "that") -> str:
    if len(text) < min_len or len(text) > max_len:
        raise ValidationError(f"{field} must be between {min_len} and {max_len} characters")
    return text


def require_range(
    value: float, *, min_value: float | None = None, max_value: float | None = None, field: str = "that",
) -> float:
    if min_value is not None and value < min_value:
        raise ValidationError(f"{field} must be at least {min_value}")
    if max_value is not None and value > max_value:
        raise ValidationError(f"{field} must be at most {max_value}")
    return value


def require_int(
    text: Any, *, min_value: int | None = None, max_value: int | None = None, field: str = "that",
) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError) as err:
        raise ValidationError(f"{field} must be a whole number") from err
    return int(require_range(value, min_value=min_value, max_value=max_value, field=field))


class Cooldown:
    """A fixed cooldown keyed by whatever bucket key `check` is given: a user id, a channel id, or a fixed constant."""

    def __init__(
        self, seconds: float, *, clock: Callable[[], float] = time.monotonic,
        format_remaining: Callable[[float], str] | None = None,
    ) -> None:
        self.seconds = seconds
        self._clock = clock
        self._format = format_remaining or (lambda remaining: f"slow down - try again in {remaining}s")
        self._last: dict[Hashable, float] = {}

    def check(self, key: Hashable) -> str | None:
        """Returns a reply string if `key` is on cooldown, else None and records this use."""
        now = self._clock()
        last = self._last.get(key)
        if last is not None:
            remaining = self.seconds - (now - last)
            if remaining > 0:
                return self._format(round(remaining, 1))
        self._last[key] = now
        return None

    def reset(self, key: Hashable) -> None:
        self._last.pop(key, None)


class RateLimiter:
    """A per-user sliding-window rate limit, more burst-friendly than Cooldown."""

    def __init__(
        self, limit: int, window_seconds: float, *, clock: Callable[[], float] = time.monotonic,
        format_remaining: Callable[[float], str] | None = None,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._format = format_remaining or (lambda remaining: f"slow down - try again in {remaining}s")
        self._hits: dict[Hashable, list[float]] = {}

    def check(self, user_id: Hashable) -> str | None:
        now = self._clock()
        cutoff = now - self.window
        hits = [t for t in self._hits.get(user_id, []) if t > cutoff]
        if len(hits) >= self.limit:
            retry = round(hits[0] + self.window - now, 1)
            self._hits[user_id] = hits
            return self._format(retry)
        hits.append(now)
        self._hits[user_id] = hits
        return None


class Quota:
    """A per-user count of stateful things held at once (e.g. active reminders)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[Hashable, int] = {}

    def check(self, user_id: Hashable) -> str | None:
        if self._counts.get(user_id, 0) >= self.limit:
            return f"you already have {self.limit} of these - remove one first"
        return None

    def use(self, user_id: Hashable) -> None:
        self._counts[user_id] = self._counts.get(user_id, 0) + 1

    def release(self, user_id: Hashable) -> None:
        self._counts[user_id] = max(0, self._counts.get(user_id, 0) - 1)

    def set_count(self, user_id: Hashable, count: int) -> None:
        self._counts[user_id] = count
