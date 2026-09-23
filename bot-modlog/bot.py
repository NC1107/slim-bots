#!/usr/bin/env python3
"""A slim-m bot that mirrors moderation actions into a channel as a readable
audit trail: timeouts, kicks, restores, and role grants/revokes.

Run it with a bot token from Space settings -> Bots and the channel it
should post into:

    pip install websockets
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_LOG_CHANNEL=<channel-uuid> python3 bot.py

`examples/bot-ping/`, `examples/bot-reminders/` and `examples/bot-roles/` all
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
  ever replay one. The one route that could serve as a catch-up feed,
  `GET /reports/history`, needs `MANAGE_MESSAGES`; this bot calls it once at
  startup anyway and logs plainly whether it got a real page back or a 403,
  rather than silently doing nothing either way. If it is down when a kick
  happens, that kick is not in the log when it comes back, and nothing about
  the reconnect says so - no gap marker, no missed-events count, nothing.
  Contrast this with `examples/bot-reminders/`, where a dropped socket is
  invisible to the *feature* precisely because `seq` and `/sync` make it
  invisible to the *bot*. Here it is invisible to the bot too, which is the
  finding: for this event family, "eventually consistent" is not the
  right description, because there is no later event that carries what was
  missed. A moderation log built this way is a *live* feed with a silent,
  permanent hole for every reconnect gap, not an eventually-complete one.
  Anyone who needs a true audit trail should read `GET /reports/history`
  with a moderator's own credential instead of trusting a bot's transcript.

Like every other example here, a frame this bot does not recognise is
ignored, and there is no cursor to persist across a process restart - only
the in-memory name/role cache, which simply starts cold again.

One more thing found live, worth naming: timing this bot's own account out
makes its very next log post fail. A timeout blocks `SEND_MESSAGES`
immediately, before the member even hears about it, so the message
reporting "you were timed out" can itself land inside the timeout window and
come back `403`. `listen()` catches that per-frame rather than letting it
kill the socket - a reconnect here is strictly worse, since it risks the
one thing this bot cannot recover from: missing whatever else happens during
the gap that follows.
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

import websockets

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
LOG_CHANNEL = os.environ.get("SLIMM_LOG_CHANNEL", "")
PROTOCOL = 1
MAX_BACKOFF_SECONDS = 60
WATCHED_TYPES = {
    "member.timeout",
    "member.removed",
    "member.restored",
    "member.role_changed",
    "role.changed",
}
# urllib's default UA is blocked by a CDN before it ever reaches slim-m.
USER_AGENT = "slimm-bot-modlog/1.0"

# user_id -> display_name, filled lazily and never invalidated (a mid-run rename keeps the old name).
_names = {}
# user_id -> set of role_ids last observed, used to infer a member.role_changed event's direction.
_last_roles = {}
# role_id -> role name, learned only from member profiles; see the docstring's GET /roles note.
_role_names = {}


def call(method, path, body=None):
    """One authenticated REST call, returning parsed JSON or None for 204."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("authorization", f"Bearer {TOKEN}")
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def socket_url(base):
    """See bot-ping's identical helper for why plaintext ws is refused off
    loopback."""
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https":
        return urllib.parse.urlunsplit(("wss", parts.netloc, "/ws", "", ""))
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"):
        print("warning: plaintext ws, loopback only", file=sys.stderr)
        return urllib.parse.urlunsplit(("ws", parts.netloc, "/ws", "", ""))
    raise RuntimeError(
        f"refusing to send a bot token over {parts.scheme or 'no'} scheme to "
        f"{parts.hostname or base}; use https"
    )


def post(text):
    """Posts a log line. `id` is a fresh UUID each call: unlike a reply to a
    specific command, there is nothing here worth deduplicating a retry
    against, so idempotency on a stable id would only risk swallowing two
    genuinely different events that happened to produce the same text."""
    call("POST", f"/channels/{LOG_CHANNEL}/messages", {"id": str(uuid.uuid4()), "content": text})


def resolve_member(user_id):
    """Fetches a member's current display name and roles, seeding both the
    name cache and the role-name map as a side effect. `None` if the account
    is gone outright (never true for a mere removal from the Space, only for
    an actually deleted account)."""
    try:
        user = call("GET", f"/users/{user_id}")
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


def handle_role_change(user_id, role_id):
    """`member.role_changed` never says grant or revoke; infer it from the
    member's current role set against what was last observed for them."""
    user = resolve_member(user_id)
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
    post(f"{name_of(user_id)} {verb} {label}")


def handle_role_definition_change(role_id):
    name = role_name_of(role_id)
    if name is not None:
        post(f"role '{name}' ({role_id}) changed - created, renamed, re-permissioned, or deleted")
    else:
        post(
            f"role {role_id} changed, but its name cannot be resolved here - "
            "GET /roles needs MANAGE_ROLES, which this bot does not hold"
        )


def handle_frame(frame):
    kind = frame.get("type")
    if kind == "member.timeout":
        user_id = frame["user_id"]
        resolve_member(user_id)
        until = frame.get("until")
        if until is None:
            post(f"{name_of(user_id)}'s timeout was lifted")
        else:
            post(f"{name_of(user_id)} was timed out")
    elif kind == "member.removed":
        user_id = frame["user_id"]
        resolve_member(user_id)
        post(f"{name_of(user_id)} was removed from the Space")
    elif kind == "member.restored":
        user_id = frame["user_id"]
        resolve_member(user_id)
        post(f"{name_of(user_id)} was let back into the Space")
    elif kind == "member.role_changed":
        handle_role_change(frame["user_id"], frame["role_id"])
    elif kind == "role.changed":
        handle_role_definition_change(frame["role_id"])


def announce_catchup_capability():
    """Proves, out loud, whether this bot could ever backfill a reconnect
    gap - rather than silently having no opinion either way."""
    try:
        call("GET", "/reports/history?limit=1")
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


async def listen(on_connected):
    me = call("GET", "/me")["id"]
    print(f"connected as {me}", flush=True)
    announce_catchup_capability()

    ticket = call("POST", "/auth/ws-ticket")["ticket"]
    ws_url = socket_url(BASE)

    async with websockets.connect(ws_url, user_agent_header=USER_AGENT) as socket:
        await socket.send(json.dumps({"type": "hello", "ticket": ticket, "protocol": PROTOCOL}))
        hello = json.loads(await socket.recv())
        if hello.get("type") != "hello":
            raise RuntimeError(f"expected a hello back, got {hello}")
        print("listening", flush=True)
        on_connected()

        async for raw in socket:
            frame = json.loads(raw)
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame.get("type") not in WATCHED_TYPES:
                continue
            try:
                handle_frame(frame)
            except urllib.error.HTTPError as err:
                # See the module docstring's note on this bot's own timeout.
                print(f"could not log {frame.get('type')}: http {err.code}", file=sys.stderr)


async def main():
    if not BASE or not TOKEN or not LOG_CHANNEL:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_LOG_CHANNEL", file=sys.stderr)
        return 2

    delay = 1

    def reset_delay():
        nonlocal delay
        delay = 1

    while True:
        try:
            await listen(reset_delay)
        except urllib.error.HTTPError as err:
            # 401 means the token was revoked; there is nothing to retry.
            if err.code == 401:
                print("token rejected - revoked?", file=sys.stderr)
                return 1
            print(f"http {err.code}, retrying in {delay}s", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying in {delay}s", file=sys.stderr)
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
