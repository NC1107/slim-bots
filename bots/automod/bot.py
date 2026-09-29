#!/usr/bin/env python3
"""bot-automod: moderator-written rules (flood, links, words, mention spam) that act and log; see README.md."""

import sqlite3
import time
import uuid
from collections import defaultdict, deque

from slimbots import ApiError, Bot, Member, Permissions

bot = Bot(prefix="!", default_data_path="automod.db")

FLOOD_MESSAGES = bot.setting("AUTOMOD_FLOOD_MESSAGES", 0, type=int)
FLOOD_SECONDS = bot.setting("AUTOMOD_FLOOD_SECONDS", 10, type=int)
FLOOD_TIMEOUT = bot.setting("AUTOMOD_FLOOD_TIMEOUT", 300, type=int)
MENTION_LIMIT = bot.setting("AUTOMOD_MENTION_LIMIT", 0, type=int)
MENTION_TIMEOUT = bot.setting("AUTOMOD_MENTION_TIMEOUT", 300, type=int)
LINK_POLICY = bot.setting("AUTOMOD_LINK_POLICY", "off").lower()
LINK_DOMAINS = [d.lower() for d in bot.setting("AUTOMOD_LINK_DOMAINS", [], type=list)]
NEW_MEMBER_HOURS = bot.setting("AUTOMOD_NEW_MEMBER_LINK_HOURS", 0, type=int)
WORDS = [w.lower() for w in bot.setting("AUTOMOD_WORDS", [], type=list)]
WORD_CHANNELS = [c.lower() for c in bot.setting("AUTOMOD_WORD_CHANNELS", [], type=list)]
LOG_CHANNEL = bot.setting("AUTOMOD_LOG_CHANNEL", "")

EXEMPT_PERMISSIONS = Permissions.MANAGE_MESSAGES

# user_id -> send times inside the flood window; memory only, a restart forgives everyone.
_recent = defaultdict(deque)
_log_channel_id = None


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, rule TEXT NOT NULL)")
    conn.commit()


def _strip_punctuation(token):
    return token.strip("<>()[]{}\"'.,;:!?")


def link_domains(text):
    """Host of every http(s):// or www. token in `text`, lowercased, without a leading `www.` or a port."""
    hosts = []
    for token in text.split():
        token = _strip_punctuation(token)
        lowered = token.lower()
        if lowered.startswith(("http://", "https://")):
            rest = token.split("://", 1)[1]
        elif lowered.startswith("www."):
            rest = token
        else:
            continue
        host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0].lower()
        if host:
            hosts.append(host[4:] if host.startswith("www.") else host)
    return hosts


def domain_listed(host, listed):
    return any(host == d or host.endswith("." + d) for d in listed)


def mention_count(text):
    return sum(1 for token in text.split() if len(token) > 1 and token.startswith("@") and token[1].isalnum())


def words_hit(text):
    """The first listed word that appears as a whole word, or None; punctuation around a word does not hide it."""
    tokens = {_strip_punctuation(t) for t in text.lower().split()}
    return next((w for w in WORDS if w in tokens), None)


def blocked_link(host):
    if LINK_POLICY == "deny":
        return domain_listed(host, LINK_DOMAINS)
    if LINK_POLICY == "allow":
        return not domain_listed(host, LINK_DOMAINS)
    return False


def flood_tripped(user_id, now):
    if FLOOD_MESSAGES <= 0:
        return False
    times = _recent[user_id]
    times.append(now)
    while times and now - times[0] > FLOOD_SECONDS:
        times.popleft()
    return len(times) > FLOOD_MESSAGES


def is_new_member(member, now):
    """Whether the server says `member` joined less than `NEW_MEMBER_HOURS` ago; a restart does not change that."""
    joined_ms = member.joined_at
    return NEW_MEMBER_HOURS > 0 and joined_ms is not None and now - joined_ms / 1000 < NEW_MEMBER_HOURS * 3600


def verdict(message, member, channel_name, now):
    """`(rule, reason, timeout_seconds)` for the first rule the message breaks, or None; checked cheapest first."""
    text = message.get("content") or ""
    user_id = message["author_id"]
    if flood_tripped(user_id, now):
        _recent[user_id].clear()
        return "flood", f"more than {FLOOD_MESSAGES} messages in {FLOOD_SECONDS}s", FLOOD_TIMEOUT
    if MENTION_LIMIT > 0 and mention_count(text) > MENTION_LIMIT:
        return "mention-spam", f"more than {MENTION_LIMIT} mentions in one message", MENTION_TIMEOUT
    hosts = link_domains(text)
    if hosts and is_new_member(member, now):
        return "new-member-link", f"a link within {NEW_MEMBER_HOURS}h of joining", None
    bad = next((h for h in hosts if blocked_link(h)), None)
    if bad:
        return "link", f"{bad} is not allowed by the {LINK_POLICY} list", None
    word = words_hit(text) if not WORD_CHANNELS or channel_name in WORD_CHANNELS else None
    if word:
        return "word", "a listed word", None
    return None


def _log_action(conn, rule):
    conn.execute("INSERT INTO actions (ts, rule) VALUES (?, ?)", (int(time.time()), rule))
    conn.commit()


async def _channel_by_path(path):
    """`category/channel`, or a bare `channel`, matched by name case-insensitively; None when nothing matches."""
    category_name, _, channel_name = path.rpartition("/")
    await bot.space.refresh_channels()
    candidates = [c for c in bot.space.channels.values() if c.name.lower() == channel_name.lower()]
    if not category_name:
        return candidates[0].id if candidates else None
    categories = await bot.client.call("GET", "/categories") or []
    wanted = {c["id"] for c in categories if c.get("name", "").lower() == category_name.lower()}
    return next((c.id for c in candidates if c.category_id in wanted), None)


async def write_modlog(text):
    """Posts to `AUTOMOD_LOG_CHANNEL` when set; a bad path is reported on stdout once per call, never fatal."""
    global _log_channel_id
    if not LOG_CHANNEL:
        return
    if _log_channel_id is None:
        _log_channel_id = await _channel_by_path(LOG_CHANNEL)
    if _log_channel_id is None:
        print(f"no log channel: nothing matches AUTOMOD_LOG_CHANNEL={LOG_CHANNEL!r}", flush=True)
        return
    await bot.client.send(_log_channel_id, text, message_id=str(uuid.uuid4()))


def _describe(member, channel_name, rule, reason, outcomes):
    where = f" in #{channel_name}" if channel_name else ""
    done = [label for label, status in outcomes if status is None] or ["no action taken"]
    text = f"automod [{rule}] {member.mention()}{where}: {reason} - {', '.join(done)}"
    failed = [f"{label} refused ({status})" for label, status in outcomes if status is not None]
    if failed:
        text += f". could not: {', '.join(failed)} - the bot needs MANAGE_MESSAGES and KICK_MEMBERS"
    return text + f". undo a timeout with `{bot.prefix}automod lift {member.mention()}`"


async def _attempt(label, call):
    """`(label, None)` when the call went through, `(label, http_status)` when the server refused it."""
    try:
        await call
    except ApiError as err:
        return label, err.status
    return label, None


async def enforce(message, member, channel_name, rule, reason, timeout_seconds):
    """Deletes the message, times the member out when the rule asks, and logs what actually happened."""
    outcomes = [await _attempt("message deleted", bot.client.delete_message(message["channel_id"], message["id"]))]
    if timeout_seconds:
        timeout_call = bot.client.time_out_member(member.id, timeout_seconds, reason=f"automod: {rule}")
        outcomes.append(await _attempt(f"timed out {timeout_seconds}s", timeout_call))
    else:
        await bot.client.send(
            message["channel_id"], f"{member.mention()}, that message was removed by automod ({rule}).",
            message_id=str(uuid.uuid4()),
        )
    await bot.store.run(_log_action, rule)
    await write_modlog(_describe(member, channel_name, rule, reason, outcomes))


@bot.event
async def on_raw_message(message):
    author_id = message.get("author_id")
    if not author_id or author_id == bot.me_id or not message.get("id"):
        return
    if await bot.authors.is_automated(author_id):
        return
    member = await bot.space.find_member(author_id)
    if member is None or member.has_permission(EXEMPT_PERMISSIONS):
        return
    channel = bot.space.channels.get(message.get("channel_id"))
    channel_name = channel.name.lower() if channel else ""
    found = verdict(message, member, channel_name, time.time())
    if found:
        await enforce(message, member, channel_name, *found)


def _count_actions(conn):
    return conn.execute("SELECT rule, COUNT(*) FROM actions GROUP BY rule ORDER BY rule").fetchall()


def rules_summary():
    lines = []
    if FLOOD_MESSAGES > 0:
        lines.append(f"- flood: more than {FLOOD_MESSAGES} messages in {FLOOD_SECONDS}s, timeout {FLOOD_TIMEOUT}s")
    if MENTION_LIMIT > 0:
        lines.append(f"- mention spam: more than {MENTION_LIMIT} mentions, timeout {MENTION_TIMEOUT}s")
    if LINK_POLICY in ("allow", "deny"):
        lines.append(f"- links: {LINK_POLICY} list of {len(LINK_DOMAINS)} domain(s)")
    if NEW_MEMBER_HOURS > 0:
        lines.append(f"- links from members who joined in the last {NEW_MEMBER_HOURS}h")
    if WORDS:
        scope = f" in {', '.join(WORD_CHANNELS)}" if WORD_CHANNELS else ""
        lines.append(f"- words: {len(WORDS)} listed{scope}")
    return lines


@bot.command(name="automod", help="Show the rules that are on, what they did, or lift a timeout", usage="<rules|stats|lift @member>")
async def automod_cmd(ctx, sub: str, member: Member = None):
    sub = sub.lower()
    if sub == "rules":
        lines = rules_summary()
        await ctx.reply("\n".join(["active rules:", *lines]) if lines else "no rules are on; every one is off by default.")
    elif sub == "stats":
        rows = await bot.store.run(_count_actions)
        await ctx.reply("\n".join(f"- {rule}: {count}" for rule, count in rows) if rows else "nothing has fired yet.")
    elif sub == "lift" and member is not None:
        if not ctx.author.has_permission(Permissions.KICK_MEMBERS):
            await ctx.reply("only someone with Kick Members can lift a timeout.")
            return
        await bot.client.lift_member_timeout(member.id)
        await write_modlog(f"automod: {ctx.author.mention()} lifted {member.mention()}'s timeout")
        await ctx.reply(f"{member.mention()} can speak again.")
    else:
        p = bot.prefix
        await ctx.reply(f"try `{p}automod rules`, `{p}automod stats`, or `{p}automod lift @member`")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
