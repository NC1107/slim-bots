#!/usr/bin/env python3
"""A slim-m bot with a per-person chip balance, two games, transfers, and a
leaderboard: `!daily`, `!balance`, `!give`, `!flip`, `!blackjack`/`!hit`/
`!stand`, `!leaderboard`, `!help`.

Run it with a bot token from Space settings -> Bots, and the ids of the
channels it should watch:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNELS=<channel-uuid>,<channel-uuid> python3 bot.py

This borrows its shape directly from the other templates: auth, the REST
call, the websocket handshake, the cursor and the reconnect backoff all come
from the `slimbots` package (`../slimbots/`), the same plumbing every
template but `bot-ping` shares - `bot-reminders`'s shape exactly, since that
is where the cursor and `/sync` catch-up pattern is documented. The
command-trigger style is `bot-roles`'s. What is new here is that every
command either moves money or reports it, so the money-safety design is the
point of this file.

**Where chips come from.** `!daily` is the only unconditional source: a flat
500 chips, claimable once every 20 hours per account. 20 hours rather than a
strict calendar day so claiming a little early one day does not permanently
shift someone's clock forward into "always a few minutes late"; it still
caps at roughly once a day. Nothing else creates chips except a bet actually
winning, and nothing removes chips except a bet actually losing or a
transfer moving them to someone else - see "Where the house edge goes"
below for why that matters.

**Two games, deliberately different.** `!flip` is instant and binary: call
heads or tails, find out immediately. `!blackjack` has a real decision in
it (`!hit` or `!stand`, repeatedly, against a dealer showing only one card)
and a hand can span several messages, so it is the one that needs durable
per-hand state, not just a per-account balance.

**The house edge, and where it goes.** There is no house account. A win
credits more than was staked; a loss destroys what was staked. Nothing
holds the difference. What keeps the total chip supply from wandering off
under normal play is that every game has a negative expected value for the
player (see the README for the exact numbers), so on average more is
destroyed by losses than is created by wins. This is also the anti-farming
control: a fair (zero-edge) coin flip has zero expected value too, so it
would not mint money on average either, but a house edge is the honest way
to say plainly "the games are not free money," and it is what stops
`!flip all` from being a viable way to grind the leaderboard.

**Concurrency.** Every balance mutation happens inside a `BEGIN IMMEDIATE`
sqlite transaction, which takes sqlite's write lock immediately rather than
on first write, so a second writer blocks until the first commits or rolls
back rather than interleaving with it. `!flip all` resolves "all" to a
balance *inside* that transaction, and the debit itself is a single
conditional `UPDATE ... WHERE balance >= ?` whose row count says whether it
happened - there is no separate read-then-write window for a second
`!flip all` to land in. `test_concurrency.py` hammers this with real
concurrent sqlite connections rather than assuming the transaction is
enough; see its own docstring.

**A cursor, an idempotency guard, and backoff.** The `seq` cursor is
`slimbots.cursor`, the same module `bot-reminders` uses. On top of it, every
money-moving command
first does `INSERT OR IGNORE INTO processed_requests` keyed on the
triggering message's own id, inside the same transaction as the balance
change, and skips the command entirely if that insert found the row already
there. `bot-reminders` does not need this: a lost reminder is a promise
broken silently, but nothing in it can be *replayed* into a double effect.
A bet can. If this process crashes after committing a bet's balance change
but before its cursor write lands, `/sync` on the next connection replays
that same message, and without the guard it would be charged twice. Backoff
is exponential, 1s doubling to 60s, matching `bot-reminders`.

What this deliberately leaves out is documented in the README, not here -
that section is written for someone deciding whether to build on this, and
belongs where they will actually go looking.
"""

import asyncio
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import uuid

from slimbots import Client, Connection, cursor, run_forever

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNELS = {c for c in os.environ.get("SLIMM_CHANNELS", "").split(",") if c}
DB_PATH = os.environ.get("SLIMM_DB_PATH", "casino.db")
MEMBER_CACHE_SECONDS = 60
# urllib's default UA is blocked by a CDN before it ever reaches slim-m.
USER_AGENT = "slimm-bot-casino/1.0"

DAILY_AMOUNT = 500
DAILY_COOLDOWN_SECONDS = 20 * 3600
FLIP_PAYOUT_NUM = 19  # win returns 1.9x the stake, floored to a whole chip
FLIP_PAYOUT_DEN = 10

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["H", "D", "C", "S"]

TRIGGER_BALANCE = re.compile(r"^!(?:balance|bal)\s*$", re.IGNORECASE)
TRIGGER_DAILY = re.compile(r"^!daily\s*$", re.IGNORECASE)
TRIGGER_LEADERBOARD = re.compile(r"^!(?:leaderboard|top)\s*$", re.IGNORECASE)
TRIGGER_HELP = re.compile(r"^!help\s*$", re.IGNORECASE)
TRIGGER_GIVE = re.compile(r"^!give\s+(\d+)\s+@?(\S+)\s*$", re.IGNORECASE)
TRIGGER_FLIP = re.compile(r"^!flip\s+(all|\d+)\s+(heads|tails|h|t)\s*$", re.IGNORECASE)
TRIGGER_BLACKJACK = re.compile(r"^!(?:blackjack|bj)\s+(all|\d+)\s*$", re.IGNORECASE)
TRIGGER_HIT = re.compile(r"^!hit\s*$", re.IGNORECASE)
TRIGGER_STAND = re.compile(r"^!stand\s*$", re.IGNORECASE)

_member_cache = {}
_member_cache_at = 0.0


# --- durable state ---------------------------------------------------------


def open_db(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    init_db(conn)
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            user_id TEXT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0,
            last_daily INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS processed_requests (
            request_id TEXT PRIMARY KEY,
            handled_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hands (
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            stake INTEGER NOT NULL,
            player_cards TEXT NOT NULL,
            dealer_cards TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            PRIMARY KEY (channel_id, user_id)
        );
        """
    )
    cursor.init_table(conn)


def begin(conn):
    conn.execute("BEGIN IMMEDIATE")


def try_consume_request(conn, request_id):
    """Records `request_id` as handled, returning False if it already was.
    Called first thing inside every money-moving command's transaction, so a
    replayed message (see the module docstring) is a no-op the second time."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO processed_requests (request_id, handled_at) VALUES (?, ?)",
        (request_id, int(time.time())),
    )
    return cur.rowcount == 1


def begin_idempotent(conn, request_id):
    """Opens the transaction every money-moving command starts with, and
    consumes `request_id` inside it. Returns False, with the transaction
    already rolled back, if this exact request was already handled - the
    caller should just return in that case, same as a fresh command that
    turned out to be a no-op."""
    begin(conn)
    if try_consume_request(conn, request_id):
        return True
    conn.execute("ROLLBACK")
    return False


def ensure_account(conn, user_id):
    conn.execute(
        "INSERT OR IGNORE INTO accounts (user_id, balance, last_daily) VALUES (?, 0, 0)",
        (user_id,),
    )


def get_balance(conn, user_id):
    ensure_account(conn, user_id)
    row = conn.execute("SELECT balance FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    return row[0]


def resolve_amount(conn, user_id, spec):
    """`spec` is `"all"` or a digit string, guaranteed by the trigger regex.
    Reading the balance for `"all"` only inside an open transaction is what
    keeps two concurrent `!flip all` from both resolving the same stake."""
    if spec == "all":
        return get_balance(conn, user_id)
    return int(spec)


def try_debit(conn, user_id, amount):
    """Atomically subtracts `amount` if, and only if, the balance covers it.
    The check and the write are the same statement, so there is no window
    between reading a balance and spending it for a second writer to land
    in - that window is exactly what would let two concurrent bets both
    succeed against a balance that only covers one of them."""
    ensure_account(conn, user_id)
    cur = conn.execute(
        "UPDATE accounts SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, user_id, amount),
    )
    return cur.rowcount == 1


def debit_or_refuse(client, conn, channel_id, author_id, request_id, amount):
    """Tries `try_debit` inside the open transaction; on failure, rolls back
    and tells the caller they are short - the refusal every bet shares."""
    if try_debit(conn, author_id, amount):
        return True
    conn.execute("ROLLBACK")
    client.send(channel_id, "you don't have that many chips", reply_to_id=request_id)
    return False


def credit(conn, user_id, amount):
    ensure_account(conn, user_id)
    conn.execute("UPDATE accounts SET balance = balance + ? WHERE user_id = ?", (amount, user_id))


def has_hand(conn, channel_id, user_id):
    row = conn.execute(
        "SELECT 1 FROM hands WHERE channel_id = ? AND user_id = ?", (channel_id, user_id)
    ).fetchone()
    return row is not None


# --- cards -------------------------------------------------------------


def draw_card():
    return secrets.choice(RANKS) + secrets.choice(SUITS)


def card_value(card):
    rank = card[:-1]
    if rank == "A":
        return 11
    if rank in ("10", "J", "Q", "K"):
        return 10
    return int(rank)


def hand_total(cards):
    total = sum(card_value(c) for c in cards)
    aces = sum(1 for c in cards if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def render_hand(cards):
    return f"{' '.join(cards)} ({hand_total(cards)})"


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


# --- member lookup, for !give -------------------------------------------


def refresh_members(client):
    """Walks the whole member list into a username -> (id, display_name)
    cache. Usernames are unique and stable, unlike display names, which is
    why `!give` resolves on them rather than on what someone happens to be
    showing at the moment - see "Identity" in the task brief this bot was
    built against."""
    global _member_cache, _member_cache_at
    cache = {}
    after = None
    while True:
        path = "/members?limit=200" + (f"&after={after}" if after else "")
        page = client.call("GET", path)
        if not page:
            break
        for profile in page:
            cache[profile["username"].lower()] = (profile["id"], profile["display_name"])
        if len(page) < 200:
            break
        after = page[-1]["id"]
    _member_cache = cache
    _member_cache_at = time.time()


def resolve_recipient(client, spec):
    """Username (case-insensitive) or a raw user id, `@`-stripped by the
    trigger regex already. Returns `(user_id, display_name)` or None."""
    global _member_cache_at
    if time.time() - _member_cache_at > MEMBER_CACHE_SECONDS:
        refresh_members(client)
    hit = _member_cache.get(spec.lower())
    if hit is None:
        refresh_members(client)
        hit = _member_cache.get(spec.lower())
    if hit is not None:
        return hit
    try:
        uuid.UUID(spec)
    except ValueError:
        return None
    try:
        profile = client.call("GET", f"/users/{spec}")
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    return profile["id"], profile["display_name"]


# --- commands ------------------------------------------------------------


def handle_balance(client, conn, channel_id, author_id, request_id):
    client.send(channel_id, f"balance: {get_balance(conn, author_id)} chips", reply_to_id=request_id)


def handle_daily(client, conn, channel_id, author_id, request_id):
    if not begin_idempotent(conn, request_id):
        return
    ensure_account(conn, author_id)
    balance, last_daily = conn.execute(
        "SELECT balance, last_daily FROM accounts WHERE user_id = ?", (author_id,)
    ).fetchone()
    now = int(time.time())
    remaining = DAILY_COOLDOWN_SECONDS - (now - last_daily)
    if remaining > 0:
        conn.execute("ROLLBACK")
        reply = f"already claimed - try again in {format_duration(remaining)}"
        client.send(channel_id, reply, reply_to_id=request_id)
        return
    conn.execute(
        "UPDATE accounts SET balance = balance + ?, last_daily = ? WHERE user_id = ?",
        (DAILY_AMOUNT, now, author_id),
    )
    conn.execute("COMMIT")
    reply = f"claimed {DAILY_AMOUNT} chips. balance: {balance + DAILY_AMOUNT}"
    client.send(channel_id, reply, reply_to_id=request_id)


def handle_leaderboard(client, conn, channel_id, request_id):
    rows = conn.execute(
        "SELECT user_id, balance FROM accounts WHERE balance > 0 ORDER BY balance DESC LIMIT 10"
    ).fetchall()
    if not rows:
        client.send(channel_id, "nobody has any chips yet - `!daily` to start", reply_to_id=request_id)
        return
    ids = ",".join(user_id for user_id, _ in rows)
    profiles = {profile["id"]: profile["display_name"] for profile in client.call("GET", f"/users?ids={ids}")}
    lines = ["chip leaderboard:"]
    lines.extend(
        f"{i}. {profiles.get(user_id, user_id[:8])} - {balance}"
        for i, (user_id, balance) in enumerate(rows, 1)
    )
    client.send(channel_id, "\n".join(lines), reply_to_id=request_id)


def handle_help(client, channel_id, request_id):
    lines = [
        "commands:",
        "`!daily` - claim your daily chips",
        "`!balance` - see your balance",
        "`!give <amount> <username>` - send chips to someone",
        "`!flip <amount|all> <heads|tails>` - coinflip, about a 5% house edge",
        "`!blackjack <amount|all>` - deal a hand, then `!hit` or `!stand`",
        "`!leaderboard` - top balances",
    ]
    client.send(channel_id, "\n".join(lines), reply_to_id=request_id)


def handle_give(client, conn, channel_id, author_id, request_id, amount, target_spec):
    if amount < 1:
        client.send(channel_id, "give at least 1 chip", reply_to_id=request_id)
        return
    target = resolve_recipient(client, target_spec)
    if target is None:
        client.send(channel_id, f"no member called `{target_spec}` here", reply_to_id=request_id)
        return
    target_id, target_name = target
    if target_id == author_id:
        client.send(channel_id, "you can't send chips to yourself", reply_to_id=request_id)
        return
    if not begin_idempotent(conn, request_id):
        return
    ensure_account(conn, target_id)
    if not debit_or_refuse(client, conn, channel_id, author_id, request_id, amount):
        return
    credit(conn, target_id, amount)
    conn.execute("COMMIT")
    client.send(
        channel_id,
        f"sent {amount} chips to {target_name}. your balance: {get_balance(conn, author_id)}",
        reply_to_id=request_id,
    )


def handle_flip(client, conn, channel_id, author_id, request_id, amount_spec, guess):
    if not begin_idempotent(conn, request_id):
        return
    amount = resolve_amount(conn, author_id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        client.send(channel_id, "you have no chips to flip - `!daily` first", reply_to_id=request_id)
        return
    if not debit_or_refuse(client, conn, channel_id, author_id, request_id, amount):
        return
    landed = secrets.choice(("heads", "tails"))
    payout = (amount * FLIP_PAYOUT_NUM) // FLIP_PAYOUT_DEN if landed == guess else 0
    if payout:
        credit(conn, author_id, payout)
    conn.execute("COMMIT")
    balance = get_balance(conn, author_id)
    if payout:
        reply = f"the coin lands on {landed} - you called it, +{payout - amount} chips. balance: {balance}"
    else:
        reply = f"the coin lands on {landed} - you called {guess}, -{amount} chips. balance: {balance}"
    client.send(channel_id, reply, reply_to_id=request_id)


def _resolve_natural(player_natural, dealer_natural, amount):
    """3:2 on a natural blackjack, a push if the dealer has one too."""
    if player_natural and dealer_natural:
        return "push - you both had blackjack", amount
    if player_natural:
        payout = amount + (amount * 3) // 2
        return f"blackjack! +{payout - amount} chips", payout
    return "dealer has blackjack - you lose", 0


def handle_blackjack_start(client, conn, channel_id, author_id, request_id, amount_spec):
    if not begin_idempotent(conn, request_id):
        return
    if has_hand(conn, channel_id, author_id):
        conn.execute("ROLLBACK")
        client.send(channel_id, "finish your hand first - `!hit` or `!stand`", reply_to_id=request_id)
        return
    amount = resolve_amount(conn, author_id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        client.send(channel_id, "you have no chips to bet - `!daily` first", reply_to_id=request_id)
        return
    if not debit_or_refuse(client, conn, channel_id, author_id, request_id, amount):
        return
    player = [draw_card(), draw_card()]
    dealer = [draw_card(), draw_card()]
    player_natural = hand_total(player) == 21
    dealer_natural = hand_total(dealer) == 21
    if player_natural or dealer_natural:
        outcome, payout = _resolve_natural(player_natural, dealer_natural, amount)
        if payout:
            credit(conn, author_id, payout)
        conn.execute("COMMIT")
        balance = get_balance(conn, author_id)
        reply = (
            f"you: {render_hand(player)}\ndealer: {render_hand(dealer)}\n"
            f"{outcome}\nbalance: {balance}"
        )
        client.send(channel_id, reply, reply_to_id=request_id)
        return
    conn.execute(
        "INSERT INTO hands (channel_id, user_id, stake, player_cards, dealer_cards, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (channel_id, author_id, amount, ",".join(player), ",".join(dealer), int(time.time())),
    )
    conn.execute("COMMIT")
    reply = f"you: {render_hand(player)}\ndealer: {dealer[0]} ??\n`!hit` or `!stand`"
    client.send(channel_id, reply, reply_to_id=request_id)


def handle_hit(client, conn, channel_id, author_id, request_id):
    if not begin_idempotent(conn, request_id):
        return
    row = conn.execute(
        "SELECT player_cards, dealer_cards FROM hands WHERE channel_id = ? AND user_id = ?",
        (channel_id, author_id),
    ).fetchone()
    if row is None:
        conn.execute("ROLLBACK")
        reply = "you don't have a hand going - `!blackjack <amount>` to start one"
        client.send(channel_id, reply, reply_to_id=request_id)
        return
    player_raw, dealer_raw = row
    player = player_raw.split(",")
    player.append(draw_card())
    if hand_total(player) > 21:
        dealer = dealer_raw.split(",")
        conn.execute("DELETE FROM hands WHERE channel_id = ? AND user_id = ?", (channel_id, author_id))
        conn.execute("COMMIT")
        balance = get_balance(conn, author_id)
        reply = f"you: {render_hand(player)} - bust\ndealer had: {' '.join(dealer)}\nbalance: {balance}"
        client.send(channel_id, reply, reply_to_id=request_id)
        return
    conn.execute(
        "UPDATE hands SET player_cards = ? WHERE channel_id = ? AND user_id = ?",
        (",".join(player), channel_id, author_id),
    )
    conn.execute("COMMIT")
    client.send(channel_id, f"you: {render_hand(player)}\n`!hit` or `!stand`", reply_to_id=request_id)


def handle_stand(client, conn, channel_id, author_id, request_id):
    if not begin_idempotent(conn, request_id):
        return
    row = conn.execute(
        "SELECT stake, player_cards, dealer_cards FROM hands WHERE channel_id = ? AND user_id = ?",
        (channel_id, author_id),
    ).fetchone()
    if row is None:
        conn.execute("ROLLBACK")
        reply = "you don't have a hand going - `!blackjack <amount>` to start one"
        client.send(channel_id, reply, reply_to_id=request_id)
        return
    stake, player_raw, dealer_raw = row
    player = player_raw.split(",")
    dealer = dealer_raw.split(",")
    player_total = hand_total(player)
    while hand_total(dealer) < 17:
        dealer.append(draw_card())
    dealer_total = hand_total(dealer)
    if dealer_total > 21 or dealer_total < player_total:
        outcome, payout = "you win", stake * 2
    elif dealer_total > player_total:
        outcome, payout = "dealer wins", 0
    else:
        outcome, payout = "push", stake
    if payout:
        credit(conn, author_id, payout)
    conn.execute("DELETE FROM hands WHERE channel_id = ? AND user_id = ?", (channel_id, author_id))
    conn.execute("COMMIT")
    balance = get_balance(conn, author_id)
    reply = f"you: {render_hand(player)}\ndealer: {render_hand(dealer)}\n{outcome}\nbalance: {balance}"
    client.send(channel_id, reply, reply_to_id=request_id)


def handle_message(client, conn, me, channel_id, message):
    author_id = message.get("author_id")
    if author_id is None or author_id == me:
        return
    content = (message.get("content") or "").strip()
    request_id = message.get("id")
    if not request_id:
        return

    if TRIGGER_BALANCE.match(content):
        handle_balance(client, conn, channel_id, author_id, request_id)
    elif TRIGGER_DAILY.match(content):
        handle_daily(client, conn, channel_id, author_id, request_id)
    elif TRIGGER_LEADERBOARD.match(content):
        handle_leaderboard(client, conn, channel_id, request_id)
    elif TRIGGER_HELP.match(content):
        handle_help(client, channel_id, request_id)
    elif match := TRIGGER_GIVE.match(content):
        handle_give(client, conn, channel_id, author_id, request_id, int(match.group(1)), match.group(2))
    elif match := TRIGGER_FLIP.match(content):
        guess = "heads" if match.group(2).lower().startswith("h") else "tails"
        handle_flip(client, conn, channel_id, author_id, request_id, match.group(1), guess)
    elif match := TRIGGER_BLACKJACK.match(content):
        handle_blackjack_start(client, conn, channel_id, author_id, request_id, match.group(1))
    elif TRIGGER_HIT.match(content):
        handle_hit(client, conn, channel_id, author_id, request_id)
    elif TRIGGER_STAND.match(content):
        handle_stand(client, conn, channel_id, author_id, request_id)


# --- connection lifecycle --------------------------------------------------


def resync(client, conn):
    """Catches up every scoped channel over `/sync` before the socket opens,
    so a command sent while the previous session was offline is not lost."""
    scopes = [{"channel_id": c, "after_seq": cursor.get(conn, c)} for c in CHANNELS]
    me = client.me()["id"]
    for scope in cursor.sync(client, scopes):
        channel_id = scope["channel_id"]
        for message in scope["messages"]:
            handle_message(client, conn, me, channel_id, message)
        if scope["messages"]:
            cursor.set(conn, channel_id, scope["messages"][-1]["seq"])
        elif scope["reset"]:
            cursor.bootstrap(client, conn, channel_id)


async def attempt(client, conn, reset_delay):
    me = client.me()["id"]
    print(f"connected as {me}", flush=True)

    for channel_id in CHANNELS:
        cursor.bootstrap(client, conn, channel_id)
    resync(client, conn)

    async with await Connection.open(client) as socket:
        print("listening", flush=True)
        reset_delay()

        async for frame in socket.frames():
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame.get("type") != "message.created":
                continue
            channel_id = frame.get("channel_id")
            if channel_id not in CHANNELS:
                continue
            message = frame.get("message") or {}
            handle_message(client, conn, me, channel_id, message)
            seq = message.get("seq")
            if seq is not None:
                cursor.set(conn, channel_id, seq)


async def main():
    if not BASE or not TOKEN or not CHANNELS:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_CHANNELS", file=sys.stderr)
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    conn = open_db(DB_PATH)

    return await run_forever(lambda reset_delay: attempt(client, conn, reset_delay))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
