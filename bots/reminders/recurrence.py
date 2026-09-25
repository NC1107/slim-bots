"""Recurring-reminder scheduling and per-user timezones; see README.md."""

import datetime as dt
from zoneinfo import ZoneInfo

WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
WEEKDAY_ALIASES = {
    "mon": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "wed": "wednesday",
    "weds": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}


def normalize_weekday(name):
    """A weekday name or common abbreviation -> 0 (Monday) through 6 (Sunday), or None."""
    name = WEEKDAY_ALIASES.get(name.lower(), name.lower())
    return WEEKDAY_NAMES.get(name)


def is_valid_timezone(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def local_clock_time(now_epoch, hour, minute, tz_name):
    """The next UTC epoch second `HH:MM` falls on in `tz_name`, today or tomorrow if already past."""
    tz = ZoneInfo(tz_name)
    now = dt.datetime.fromtimestamp(now_epoch, tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return int(candidate.timestamp())


def next_weekly(reference_epoch, weekday, hour, minute, tz_name):
    """The next occurrence of `weekday` at `HH:MM` in `tz_name`, strictly after `reference_epoch`."""
    tz = ZoneInfo(tz_name)
    reference = dt.datetime.fromtimestamp(reference_epoch, tz)
    days_ahead = (weekday - reference.weekday()) % 7
    candidate = (reference + dt.timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= reference:
        candidate += dt.timedelta(days=7)
    return int(candidate.timestamp())


def next_interval(last_due_at, interval_seconds, now_epoch):
    """The next multiple of `interval_seconds` after `last_due_at` that still lies after `now_epoch`."""
    next_at = last_due_at + interval_seconds
    if next_at <= now_epoch:
        missed = (now_epoch - next_at) // interval_seconds + 1
        next_at += missed * interval_seconds
    return next_at


def format_local(epoch_seconds, tz_name):
    tz = ZoneInfo(tz_name)
    return dt.datetime.fromtimestamp(epoch_seconds, tz).strftime("%Y-%m-%d %H:%M %Z")
