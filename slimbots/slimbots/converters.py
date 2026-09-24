"""`Duration` and `TimeOfDay`: argument types `Command._convert` recognizes beyond int/float/str/Member."""

_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class Duration(int):
    """A count of seconds parsed from `10m`, `2h30m`, `1d`; behaves as a plain int once converted."""

    @classmethod
    def parse(cls, token):
        total = 0
        rest = token
        matched_anything = False
        while rest:
            digits = ""
            while rest and rest[0].isdigit():
                digits += rest[0]
                rest = rest[1:]
            if not digits or not rest or rest[0] not in _DURATION_UNIT_SECONDS:
                return None
            total += int(digits) * _DURATION_UNIT_SECONDS[rest[0]]
            rest = rest[1:]
            matched_anything = True
        return cls(total) if matched_anything and total > 0 else None


class TimeOfDay:
    """A wall-clock `HH:MM`, with no date or timezone attached."""

    def __init__(self, hour, minute):
        self.hour = hour
        self.minute = minute

    @classmethod
    def parse(cls, token):
        hour_text, sep, minute_text = token.partition(":")
        if not sep or not hour_text.isdigit() or not minute_text.isdigit():
            return None
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        return cls(hour, minute)

    def __repr__(self):
        return f"TimeOfDay({self.hour:02d}:{self.minute:02d})"
