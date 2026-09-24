#!/usr/bin/env python3
"""bot-modlog: mirrors timeouts, kicks, restores, and role changes into a channel; see README.md."""

import sqlite3
import time
import uuid
from datetime import datetime, timezone

from slimbots import ApiError, Bot, Embed
from slimbots.http import is_forbidden

# A reconnect faster than this is not worth a channel post - most drops are a blip that missed nothing worth naming.
GAP_NOTICE_THRESHOLD_SECONDS = 5
MAX_GAPS_SHOWN = 10

bot = Bot(prefix="!", require_channels=True, default_data_path="modlog.db")
bot.db = None

# user_id -> set of role_ids last observed, used to infer a member.role_changed event's direction.
_last_roles = {}
# role_id -> role name, learned only from member profiles (GET /roles needs MANAGE_ROLES, deliberately not held).
_role_names = {}
# Wall-clock time this bot last knew for certain it was connected - None until the first successful connect.
_last_seen_at = None


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reconnected_at INTEGER NOT NULL,
            downtime_seconds INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def format_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


def format_until(until_ms):
    """`until` is Unix milliseconds; render the wall-clock time and a human duration together."""
    until_s = until_ms / 1000
    when = datetime.fromtimestamp(until_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    remaining = until_s - time.time()
    if remaining <= 0:
        return f"until {when} (already past)"
    return f"until {when} ({format_duration(remaining)} from now)"


def record_event(kind, text):
    bot.db.execute("INSERT INTO events (ts, kind, text) VALUES (?, ?, ?)", (int(time.time()), kind, text))
    bot.db.commit()


async def post(kind, text):
    """Posts a log line under a fresh id every call - two different events can produce identical text,
    so there is nothing here worth deduplicating against the way a command reply is."""
    embed = Embed(footer=kind)
    await bot.client.send(bot.channel, text, message_id=str(uuid.uuid4()), embeds=[embed.to_wire()], fallback_content=text)
    record_event(kind, text)


def note_alive():
    global _last_seen_at
    _last_seen_at = time.time()


async def report_reconnect_gap():
    """Posts and records downtime since the last frame seen, unless this is the first connect or the gap was too short."""
    global _last_seen_at
    if _last_seen_at is None:
        return
    downtime = int(time.time() - _last_seen_at)
    if downtime < GAP_NOTICE_THRESHOLD_SECONDS:
        return
    bot.db.execute("INSERT INTO gaps (reconnected_at, downtime_seconds) VALUES (?, ?)", (int(time.time()), downtime))
    bot.db.commit()
    await post(
        "gap",
        f"reconnected after approximately {format_duration(downtime)} offline - moderation events during that "
        "gap are not recorded here (`!modlog permissions` says what would close it)",
    )


async def resolve_member(user_id):
    """Fetches a member's current roles, seeding the role-name map; None if the account is gone outright."""
    try:
        member = await bot.space.fetch_member(user_id)
    except ApiError as err:
        if err.status == 404:
            return None
        raise
    for role_id, name in zip(member.role_ids, member.roles):
        _role_names[role_id] = name
    return member


def name_of(user_id):
    member = bot.space.members.get(user_id)
    return member.display_name if member else user_id


def role_name_of(role_id):
    return _role_names.get(role_id)


async def handle_role_change(user_id, role_id):
    """`member.role_changed` never says grant or revoke; infer it from the member's role set against what was last seen."""
    member = await resolve_member(user_id)
    current = set(member.role_ids) if member else set()
    previous = _last_roles.get(user_id)
    _last_roles[user_id] = current
    label = role_name_of(role_id) or f"role {role_id}"

    if previous is None:
        verb = "now holds" if role_id in current else "does not hold"
    elif role_id in current and role_id not in previous:
        verb = "was granted"
    elif role_id not in current and role_id in previous:
        verb = "was revoked from"
    else:
        verb = "role membership changed for"  # two changes to this role collapsed between our two reads
    await post("member.role_changed", f"{name_of(user_id)} {verb} {label}")


async def show_stats(ctx):
    rows = bot.db.execute("SELECT kind, COUNT(*) FROM events WHERE kind != 'gap' GROUP BY kind ORDER BY kind").fetchall()
    if not rows:
        await ctx.reply("nothing recorded yet.")
        return
    total = sum(count for _, count in rows)
    lines = [f"{total} event(s) recorded locally since this bot's database was created:"]
    lines.extend(f"- {kind}: {count}" for kind, count in rows)
    lines.append("this is only what this bot itself saw live, never a substitute for a real audit trail.")
    await ctx.reply("\n".join(lines))


async def show_gaps(ctx):
    rows = bot.db.execute(
        "SELECT reconnected_at, downtime_seconds FROM gaps ORDER BY id DESC LIMIT ?", (MAX_GAPS_SHOWN,)
    ).fetchall()
    if not rows:
        await ctx.reply("no reconnect gaps recorded.")
        return
    lines = [f"last {len(rows)} known gap(s), most recent first:"]
    for reconnected_at, downtime in rows:
        when = datetime.fromtimestamp(reconnected_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"- reconnected {when} after ~{format_duration(downtime)} offline")
    lines.append("moderation events during any of these gaps are permanently missing from this log.")
    await ctx.reply("\n".join(lines))


async def show_permissions(ctx):
    await ctx.reply(
        "I hold only VIEW_CHANNEL and SEND_MESSAGES on this channel. Two more would change what I can do:\n"
        "- MANAGE_MESSAGES: GET /reports/history becomes readable, which is the reason text behind a timeout "
        "or removal, and a way to actually backfill a reconnect gap instead of leaving it permanent.\n"
        "- MANAGE_ROLES: GET /roles becomes readable, so a role's name resolves even for one nobody currently "
        "holds, instead of only through a member profile that happens to carry it."
    )


@bot.command(name="modlog", help="stats/gaps/permissions - this bot's own local transcript", usage="<stats|gaps|permissions>")
async def modlog_cmd(ctx, sub: str):
    sub = sub.lower()
    if sub == "stats":
        await show_stats(ctx)
    elif sub == "gaps":
        await show_gaps(ctx)
    elif sub == "permissions":
        await show_permissions(ctx)
    else:
        await ctx.reply("try `!modlog stats`, `!modlog gaps`, or `!modlog permissions`")


@bot.event
async def on_frame(frame):
    note_alive()


@bot.event
async def on_member_timeout(frame):
    user_id = frame["user_id"]
    await resolve_member(user_id)
    until = frame.get("until")
    if until is None:
        await post("member.timeout", f"{name_of(user_id)}'s timeout was lifted")
    else:
        await post("member.timeout", f"{name_of(user_id)} was timed out {format_until(until)}")


@bot.event
async def on_member_removed(frame):
    user_id = frame["user_id"]
    await resolve_member(user_id)
    await post("member.removed", f"{name_of(user_id)} was removed from the Space")


@bot.event
async def on_member_restored(frame):
    user_id = frame["user_id"]
    await resolve_member(user_id)
    await post("member.restored", f"{name_of(user_id)} was let back into the Space")


@bot.event
async def on_member_role_changed(frame):
    await handle_role_change(frame["user_id"], frame["role_id"])


@bot.event
async def on_role_changed(frame):
    role_id = frame["role_id"]
    name = role_name_of(role_id)
    if name is not None:
        await post("role.changed", f"role '{name}' ({role_id}) changed - created, renamed, re-permissioned, or deleted")
    else:
        await post(
            "role.changed",
            f"role {role_id} changed, but its name cannot be resolved here - GET /roles needs MANAGE_ROLES, "
            "which this bot does not hold",
        )


async def announce_catchup_capability():
    """Proves, out loud, whether this bot could ever backfill a reconnect gap - rather than silently having no opinion."""
    try:
        await bot.client.call("GET", "/reports/history?limit=1")
        print("catch-up available: /reports/history is readable", flush=True)
    except ApiError as err:
        if not is_forbidden(err):
            raise
        print(
            "no catch-up available: /reports/history needs MANAGE_MESSAGES, which this bot does not hold - "
            "a reconnect gap in the moderation log is permanent",
            flush=True,
        )


@bot.event
async def on_connect():
    await announce_catchup_capability()
    await report_reconnect_gap()
    note_alive()


def main():
    bot.db = sqlite3.connect(bot.data_path)
    init_db(bot.db)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
