#!/usr/bin/env python3
"""A slim-m bot that mirrors moderation actions into a channel as a readable
audit trail: timeouts, kicks, restores, and role grants/revokes.

Run it with a bot token from Space settings -> Bots and the channel it
should post into:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_LOG_CHANNEL=<channel-uuid> python3 bot.py

`bot-ping/`, `bot-reminders/` and `bot-roles/` all
watch message traffic. This one watches five events none of them touch:
`member.timeout`, `member.removed`, `member.restored`, `member.role_changed`
and `role.changed` (`crates/slimm-server/src/hub/event.rs`'s
`MemberTimeoutChanged`, `MemberRemoved`, `MemberRestored`,
`MemberRoleChanged` and `RoleChanged`). All five are deployment-wide: they
reach every connected session regardless of channel permission, and
`crates/slimm-server/src/http/ws/authorization.rs` delivers them
unconditionally, with no `KICK_MEMBERS`/`BAN_MEMBERS`/`MANAGE_ROLES` check at
all. This bot only holds `VIEW_CHANNEL` and `SEND_MESSAGES` on the one
channel it posts into, and that turns out to be enough to see every
moderation action in the whole deployment - proven live against a bot token
holding none of those four bits.

Auth, the REST call, the websocket handshake and the reconnect loop with
backoff come from the `slimbots` package (`../slimbots/`) - the same
plumbing every template but `bot-ping` shares. Everything below this point
is this bot's own event-handling logic, which the library has no opinion on.

What the wire frames do not say, and what this bot has to work around:

- **`member.role_changed` carries no direction.** The wire type
  (`ServerFrame::MemberRoleChanged { user_id, role_id }`) does not say
  whether the role was granted or revoked, and neither does the internal
  `Event` it comes from. This bot infers it by fetching the member's current
  `role_ids` (`GET /users/{id}`, which any authenticated caller may read) and
  diffing against what it last saw for that user. The first time a user is
  seen there is nothing to diff against, so that one report reads "now
  holds" rather than "granted" - a startup cost paid once per user, not a
  recurring gap.
- **A role's name is not always resolvable.** `GET /roles` - the only route
  that lists every role by id - requires `MANAGE_ROLES`
  (`crates/slimm-server/src/http/roles.rs`), which this bot deliberately does
  not hold. A role name usually still resolves anyway, because
  `GET /users/{id}` returns each member's `roles` (names) alongside
  `role_ids`, positionally matched, and this bot builds its id-to-name map
  from that as a side effect of resolving `member.role_changed`. A
  `role.changed` for a role this bot has never seen held by anyone it has
  looked up - most commonly a brand new, still-empty role, or one just
  deleted - logs the bare id instead, with a one-line note why. This is not
  a workaround for a bug: `GET /roles` also exposes each role's raw
  permission bits, and gating the one route that hands those out is the
  intended boundary.
- **No reason ever reaches the wire.** A timeout or removal's `reason` lives
  in `moderation_audit_log` and only comes back over `GET /reports/history`,
  which requires `MANAGE_MESSAGES` - also deliberately not held here. This
  bot's log says who and when, never why.
- **There is no catching up.** None of these five events carry a `seq`, so
  there is nothing to persist as a cursor and no `/sync` scope that could
  ever replay one - this bot does not reach for `slimbots.cursor` at all,
  because there is nothing it could store that `/sync` would ever answer for
  these event types. The one route that could serve as a catch-up feed,
  `GET /reports/history`, needs `MANAGE_MESSAGES`; this bot calls it once at
  startup anyway and logs plainly whether it got a real page back or a 403,
  rather than silently doing nothing either way. If it is down when a kick
  happens, that kick is not in the log when it comes back, and nothing about
  the reconnect says so - no gap marker, no missed-events count, nothing.
  Contrast this with `bot-reminders/`, where a dropped socket is
  invisible to the *feature* precisely because `seq` and `/sync` make it
  invisible to the *bot*. Here it is invisible to the bot too, which is the
  finding: for this event family, "eventually consistent" is not the
  right description, because there is no later event that carries what was
  missed. A moderation log built this way is a *live* feed with a silent,
  permanent hole for every reconnect gap, not an eventually-complete one.
  Anyone who needs a true audit trail should read `GET /reports/history`
  with a moderator's own credential instead of trusting a bot's transcript.

A frame this bot does not recognise is ignored, the same as every other
template. Two different kinds of state survive a process restart and one
does not: `SLIMM_DB_PATH` (default `modlog.db`) holds a local record of
every event this bot itself has logged and every reconnect gap it noticed,
plus the ordinary `seq` cursor for its own command traffic (see "Commands"
below) - none of that is a substitute for `GET /reports/history`, it is
just this bot's own transcript, kept queryable across restarts. The
in-memory name/role cache is not persisted and simply starts cold again,
since it is cheap to rebuild from the next few events.

## Commands

Unlike the moderation events above, `message.created` on `SLIMM_LOG_CHANNEL`
does carry a `seq`, so this half of the bot can actually catch up on a
command sent while disconnected, via `slimbots.cursor` the same way
`bot-roles` does for its own channel:

- `!modlog stats` - counts of every event this bot has logged locally, by
  kind, since `SLIMM_DB_PATH` was created. Explicitly labelled as this bot's
  own transcript, not an audit trail.
- `!modlog gaps` - the last several reconnect gaps this bot noticed (see
  below), each a permanent hole in the log rather than something later
  events fill in.
- `!modlog permissions` - exactly what `MANAGE_MESSAGES` and `MANAGE_ROLES`
  would each add, on demand, so an admin does not have to read this
  docstring to find out.

## Reconnect gaps are now visible, not just documented

Every earlier version of this bot noted at startup, once, to its own stdout,
whether `GET /reports/history` was readable. That is not the same as an
admin reading the channel noticing anything: a moderation action that
happens during a dropped socket used to vanish with no trace at all, not
even a marker saying something was probably missed. Now, whenever this bot
reconnects after a previous successful connection - never on the very first
connect, which is not a gap - it posts how long it was offline, right in the
log, and records it in `SLIMM_DB_PATH` for `!modlog gaps` to answer later.
That downtime estimate is measured from the last frame this bot actually
saw (or the last successful connect, if no frame arrived first) to the
moment the new connection is confirmed live; it can only ever be a decent
estimate, since nothing on the wire says precisely when the drop happened.

One more thing found live, worth naming: timing this bot's own account out
makes its very next log post fail. A timeout blocks `SEND_MESSAGES`
immediately, before the member even hears about it, so the message
reporting "you were timed out" can itself land inside the timeout window and
come back `403`. `listen()` catches that per-frame rather than letting it
kill the socket - a reconnect here is strictly worse, since it risks the
one thing this bot cannot recover from: missing whatever else happens during
the gap that follows. This bot also deliberately does not use `Client.send`'s
built-in retry for its own log posts: retrying a log line under one fixed id
is right for a reply to a specific command, but there is nothing here worth
deduplicating against, since two genuinely different events could produce
identical text.
"""

import asyncio
import os
import re
import sqlite3
import sys
import time
import urllib.error
import uuid
from datetime import datetime, timezone

from slimbots import Client, Connection, cursor, run_forever

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
LOG_CHANNEL = os.environ.get("SLIMM_LOG_CHANNEL", "")
DB_PATH = os.environ.get("SLIMM_DB_PATH", "modlog.db")
USER_AGENT = "slimm-bot-modlog/1.0"
WATCHED_TYPES = {
    "member.timeout",
    "member.removed",
    "member.restored",
    "member.role_changed",
    "role.changed",
}
# A reconnect faster than this is not worth a channel post - most drops are
# a blip that never actually missed anything worth naming.
GAP_NOTICE_THRESHOLD_SECONDS = 5
MAX_GAPS_SHOWN = 10

TRIGGER_STATS = re.compile(r"^!modlog\s+stats\s*$", re.IGNORECASE)
TRIGGER_GAPS = re.compile(r"^!modlog\s+gaps\s*$", re.IGNORECASE)
TRIGGER_PERMISSIONS = re.compile(r"^!modlog\s+permissions\s*$", re.IGNORECASE)

# user_id -> display_name, filled lazily and never invalidated (a mid-run rename keeps the old name).
_names = {}
# user_id -> set of role_ids last observed, used to infer a member.role_changed event's direction.
_last_roles = {}
# role_id -> role name, learned only from member profiles; see the docstring's GET /roles note.
_role_names = {}
# user_id -> whether GET /users/{id} says is_bot; see bot-ping's own cache.
_is_bot_cache = {}
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
    cursor.init_table(conn)


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
    """`until` is Unix milliseconds; render both the wall-clock time and a
    human duration, since a bare timestamp makes a reader do the subtraction
    the bot could have done for them."""
    until_s = until_ms / 1000
    when = datetime.fromtimestamp(until_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    remaining = until_s - time.time()
    if remaining <= 0:
        return f"until {when} (already past)"
    return f"until {when} ({format_duration(remaining)} from now)"


def is_bot_author(client, author_id):
    """Whether `author_id` belongs to another bot; see bot-ping's own version
    of this check for why every bot in the fleet needs one."""
    if author_id not in _is_bot_cache:
        try:
            _is_bot_cache[author_id] = bool(client.call("GET", f"/users/{author_id}").get("is_bot"))
        except Exception:
            _is_bot_cache[author_id] = False
    return _is_bot_cache[author_id]


def record_event(conn, kind, text):
    conn.execute("INSERT INTO events (ts, kind, text) VALUES (?, ?, ?)", (int(time.time()), kind, text))
    conn.commit()


def post(client, conn, kind, text):
    """Posts a log line with a fresh id every call (see the module docstring
    on why this bypasses `Client.send`'s idempotency), and records it in
    `SLIMM_DB_PATH` so `!modlog stats`/`!modlog gaps` have something to
    answer from later - this is a record of what this bot itself saw, never
    a substitute for `GET /reports/history`."""
    client.call("POST", f"/channels/{LOG_CHANNEL}/messages", {"id": str(uuid.uuid4()), "content": text})
    record_event(conn, kind, text)


def note_alive():
    global _last_seen_at
    _last_seen_at = time.time()


def report_reconnect_gap(client, conn):
    """Posts and records how long this bot was offline, unless this is the
    very first connection this process has made (nothing to report) or the
    gap was too short to matter. See the module docstring's "Reconnect gaps"
    section for what this estimate can and cannot promise."""
    global _last_seen_at
    if _last_seen_at is None:
        return
    downtime = int(time.time() - _last_seen_at)
    if downtime < GAP_NOTICE_THRESHOLD_SECONDS:
        return
    conn.execute(
        "INSERT INTO gaps (reconnected_at, downtime_seconds) VALUES (?, ?)",
        (int(time.time()), downtime),
    )
    conn.commit()
    post(
        client,
        conn,
        "gap",
        f"reconnected after approximately {format_duration(downtime)} offline - moderation "
        "events during that gap are not recorded here (`!modlog permissions` says what "
        "would close it)",
    )


def show_stats(client, conn, reply_to_id):
    rows = conn.execute(
        "SELECT kind, COUNT(*) FROM events WHERE kind != 'gap' GROUP BY kind ORDER BY kind"
    ).fetchall()
    if not rows:
        client.send(LOG_CHANNEL, "nothing recorded yet.", reply_to_id=reply_to_id)
        return
    total = sum(count for _, count in rows)
    lines = [f"{total} event(s) recorded locally since this bot's database was created:"]
    lines.extend(f"- {kind}: {count}" for kind, count in rows)
    lines.append("this is only what this bot itself saw live, never a substitute for a real audit trail.")
    client.send(LOG_CHANNEL, "\n".join(lines), reply_to_id=reply_to_id)


def show_gaps(client, conn, reply_to_id):
    rows = conn.execute(
        "SELECT reconnected_at, downtime_seconds FROM gaps ORDER BY id DESC LIMIT ?", (MAX_GAPS_SHOWN,)
    ).fetchall()
    if not rows:
        client.send(LOG_CHANNEL, "no reconnect gaps recorded.", reply_to_id=reply_to_id)
        return
    lines = [f"last {len(rows)} known gap(s), most recent first:"]
    for reconnected_at, downtime in rows:
        when = datetime.fromtimestamp(reconnected_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"- reconnected {when} after ~{format_duration(downtime)} offline")
    lines.append("moderation events during any of these gaps are permanently missing from this log.")
    client.send(LOG_CHANNEL, "\n".join(lines), reply_to_id=reply_to_id)


def show_permissions(client, reply_to_id):
    text = "\n".join(
        [
            "I hold only VIEW_CHANNEL and SEND_MESSAGES on this channel. Two more would change what I can do:",
            "- MANAGE_MESSAGES: GET /reports/history becomes readable, which is the reason text behind a "
            "timeout or removal, and a way to actually backfill a reconnect gap instead of leaving it "
            "permanent.",
            "- MANAGE_ROLES: GET /roles becomes readable, so a role's name resolves even for one nobody "
            "currently holds, instead of only through a member profile that happens to carry it.",
        ]
    )
    client.send(LOG_CHANNEL, text, reply_to_id=reply_to_id)


def handle_command(client, conn, me, message):
    author_id = message.get("author_id")
    if author_id is None or author_id == me or is_bot_author(client, author_id):
        return
    content = (message.get("content") or "").strip()
    reply_to_id = message.get("id")
    if TRIGGER_STATS.match(content):
        show_stats(client, conn, reply_to_id)
    elif TRIGGER_GAPS.match(content):
        show_gaps(client, conn, reply_to_id)
    elif TRIGGER_PERMISSIONS.match(content):
        show_permissions(client, reply_to_id)


def resolve_member(client, user_id):
    """Fetches a member's current display name and roles, seeding both the
    name cache and the role-name map as a side effect. `None` if the account
    is gone outright (never true for a mere removal from the Space, only for
    an actually deleted account)."""
    try:
        user = client.call("GET", f"/users/{user_id}")
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    _names[user_id] = user["display_name"]
    for role_id, name in zip(user["role_ids"], user["roles"]):
        _role_names[role_id] = name
    return user


def name_of(user_id):
    return _names.get(user_id, user_id)


def role_name_of(role_id):
    return _role_names.get(role_id)


def handle_role_change(client, conn, user_id, role_id):
    """`member.role_changed` never says grant or revoke; infer it from the
    member's current role set against what was last observed for them."""
    user = resolve_member(client, user_id)
    current = set(user["role_ids"]) if user else set()
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
        # Two changes to this role collapsed between our two reads.
        verb = "role membership changed for"
    post(client, conn, "member.role_changed", f"{name_of(user_id)} {verb} {label}")


def handle_role_definition_change(client, conn, role_id):
    name = role_name_of(role_id)
    if name is not None:
        post(
            client,
            conn,
            "role.changed",
            f"role '{name}' ({role_id}) changed - created, renamed, re-permissioned, or deleted",
        )
    else:
        post(
            client,
            conn,
            "role.changed",
            f"role {role_id} changed, but its name cannot be resolved here - "
            "GET /roles needs MANAGE_ROLES, which this bot does not hold",
        )


def handle_frame(client, conn, frame):
    kind = frame.get("type")
    if kind == "member.timeout":
        user_id = frame["user_id"]
        resolve_member(client, user_id)
        until = frame.get("until")
        if until is None:
            post(client, conn, kind, f"{name_of(user_id)}'s timeout was lifted")
        else:
            post(client, conn, kind, f"{name_of(user_id)} was timed out {format_until(until)}")
    elif kind == "member.removed":
        user_id = frame["user_id"]
        resolve_member(client, user_id)
        post(client, conn, kind, f"{name_of(user_id)} was removed from the Space")
    elif kind == "member.restored":
        user_id = frame["user_id"]
        resolve_member(client, user_id)
        post(client, conn, kind, f"{name_of(user_id)} was let back into the Space")
    elif kind == "member.role_changed":
        handle_role_change(client, conn, frame["user_id"], frame["role_id"])
    elif kind == "role.changed":
        handle_role_definition_change(client, conn, frame["role_id"])


def announce_catchup_capability(client):
    """Proves, out loud, whether this bot could ever backfill a reconnect
    gap - rather than silently having no opinion either way."""
    try:
        client.call("GET", "/reports/history?limit=1")
        print("catch-up available: /reports/history is readable", flush=True)
    except urllib.error.HTTPError as err:
        if err.code == 403:
            print(
                "no catch-up available: /reports/history needs MANAGE_MESSAGES, "
                "which this bot does not hold - a reconnect gap in the "
                "moderation log is permanent",
                flush=True,
            )
        else:
            raise


def resync_commands(client, conn, me):
    """Catches up on `!modlog` commands sent to `LOG_CHANNEL` while
    disconnected. Unlike the moderation events this bot exists to watch,
    `message.created` carries a `seq`, so this half of the bot can actually
    recover a dropped command - the same `slimbots.cursor` pattern
    `bot-canvas-board` uses for its own channel."""
    after = cursor.get(conn, LOG_CHANNEL)
    scope = cursor.sync(client, [{"channel_id": LOG_CHANNEL, "after_seq": after}])[0]
    for message in scope["messages"]:
        handle_command(client, conn, me, message)
    if scope["messages"]:
        cursor.set(conn, LOG_CHANNEL, scope["messages"][-1]["seq"])
    elif scope["reset"]:
        cursor.bootstrap(client, conn, LOG_CHANNEL)


async def attempt(client, conn, reset_delay):
    me = client.me()["id"]
    print(f"connected as {me}", flush=True)
    announce_catchup_capability(client)
    cursor.bootstrap(client, conn, LOG_CHANNEL)
    resync_commands(client, conn, me)
    report_reconnect_gap(client, conn)

    async with await Connection.open(client) as socket:
        print("listening", flush=True)
        reset_delay()
        note_alive()

        async for frame in socket.frames():
            note_alive()
            frame_type = frame.get("type")
            if frame_type == "message.created" and frame.get("channel_id") == LOG_CHANNEL:
                message = frame.get("message") or {}
                handle_command(client, conn, me, message)
                if message.get("seq") is not None:
                    cursor.set(conn, LOG_CHANNEL, message["seq"])
                continue
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame_type not in WATCHED_TYPES:
                continue
            try:
                handle_frame(client, conn, frame)
            except urllib.error.HTTPError as err:
                # See the module docstring's note on this bot's own timeout.
                print(f"could not log {frame_type}: http {err.code}", file=sys.stderr)


async def main():
    if not BASE or not TOKEN or not LOG_CHANNEL:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_LOG_CHANNEL", file=sys.stderr)
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    return await run_forever(lambda reset_delay: attempt(client, conn, reset_delay))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
