"""Per-user cooldowns, rate limits, quotas, and input bounds.

Discord bots universally have some version of these, and the failure mode
of skipping them is not "a bit of spam" - it is one member's script turning
a bet-resolving or state-holding command into an unbounded loop. Every
check here returns a plain string on rejection, meant to be sent straight
back to the user, never a silent drop: a bot that just goes quiet under load
reads as broken, not as protected.

None of this is a cap on what a *feature* can do - a cooldown says how often
you may ask, never how good the answer is once you are allowed to ask. Put a
cooldown on the command, not a ceiling on the game.
"""

import time


class Cooldown:
    """A minimum gap between uses of one command, per user.

    One instance guards one command. `check(user_id)` returns `None` and
    records a use when the call is allowed, or a human-readable wait message
    when it is not - it never raises and never silently drops the call.
    """

    def __init__(self, seconds, *, clock=time.monotonic, format_remaining=None):
        self.seconds = seconds
        self._clock = clock
        self._format = format_remaining or (lambda s: f"{s:.0f}s")
        self._last = {}

    def check(self, user_id):
        now = self._clock()
        last = self._last.get(user_id)
        if last is not None:
            remaining = self.seconds - (now - last)
            if remaining > 0:
                return f"slow down - try again in {self._format(remaining)}"
        self._last[user_id] = now
        return None

    def reset(self, user_id):
        """Clears any recorded use, so the next `check` succeeds regardless
        of timing - for a command that was refused for an unrelated reason
        after already being charged against the cooldown."""
        self._last.pop(user_id, None)


class RateLimiter:
    """A per-user sliding window: up to `limit` uses in any `window_seconds`
    span.

    Unlike `Cooldown`, this allows a burst up to `limit` before it starts
    rejecting, which suits a command reasonably fired a few times in a row
    (a run of coinflips) rather than one that should never repeat quickly (a
    daily claim, which wants `Cooldown` instead).
    """

    def __init__(self, limit, window_seconds, *, clock=time.monotonic, format_remaining=None):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._format = format_remaining or (lambda s: f"{s:.0f}s")
        self._hits = {}

    def check(self, user_id):
        now = self._clock()
        hits = [t for t in self._hits.get(user_id, ()) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[user_id] = hits
            retry_in = self.window - (now - hits[0])
            return f"slow down - try again in {self._format(retry_in)}"
        hits.append(now)
        self._hits[user_id] = hits
        return None


class Quota:
    """A cap on how many of some stateful thing one user may hold at once -
    pending reminders, open hands, whatever a bot must not let one person
    pile up without bound.

    `check` answers whether another may be taken; `use`/`release` record
    taking and giving one back. A bot restoring durable state at startup
    (reminders loaded from sqlite, say) should call `set_count` once so the
    in-memory count matches reality instead of starting every restart back
    at zero.
    """

    def __init__(self, limit):
        self.limit = limit
        self._counts = {}

    def check(self, user_id):
        if self._counts.get(user_id, 0) >= self.limit:
            return f"you already have {self.limit} of these - remove one before adding another"
        return None

    def use(self, user_id):
        self._counts[user_id] = self._counts.get(user_id, 0) + 1

    def release(self, user_id):
        if user_id in self._counts:
            self._counts[user_id] = max(0, self._counts[user_id] - 1)

    def set_count(self, user_id, count):
        self._counts[user_id] = count


class ValidationError(ValueError):
    """Raised by the bounds helpers below. The message is meant to be sent
    straight back to the user, not logged."""


def require_len(text, *, max_len, min_len=0, field="that"):
    if len(text) < min_len:
        raise ValidationError(f"{field} is too short - needs at least {min_len} characters")
    if len(text) > max_len:
        raise ValidationError(f"{field} is too long - keep it under {max_len} characters")
    return text


def require_range(value, *, min_value=None, max_value=None, field="that"):
    if min_value is not None and value < min_value:
        raise ValidationError(f"{field} must be at least {min_value}")
    if max_value is not None and value > max_value:
        raise ValidationError(f"{field} must be at most {max_value}")
    return value


def require_int(text, *, min_value=None, max_value=None, field="that"):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} needs to be a whole number") from None
    return require_range(value, min_value=min_value, max_value=max_value, field=field)
