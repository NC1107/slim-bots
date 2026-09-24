"""bot-casino's money/db primitives and card logic, shared by its extensions; see docs/framework.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" there for why."""

import secrets
import sqlite3
import time

import blackjack
from slimbots import BadArgument

DAILY_AMOUNT = 500
DAILY_COOLDOWN_SECONDS = 20 * 3600
FLIP_PAYOUT_NUM = 19  # win returns 1.9x the stake, floored to a whole chip
FLIP_PAYOUT_DEN = 10
MAX_AMOUNT = 1_000_000_000_000  # well under sqlite's 64-bit ceiling; see README.md
PROCESSED_REQUEST_RETENTION_SECONDS = 30 * 24 * 3600
PRUNE_INTERVAL_SECONDS = 3600

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["H", "D", "C", "S"]


# --- durable state; test_concurrency.py imports these (via bot.py's re-export) by name, unchanged ---


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
        """
    )
    blackjack.init_table(conn)


def prune_processed_requests(conn, cutoff):
    conn.execute("DELETE FROM processed_requests WHERE handled_at < ?", (cutoff,))
    conn.commit()


def begin(conn):
    conn.execute("BEGIN IMMEDIATE")


def try_consume_request(conn, request_id):
    """First call inside every money-moving command's transaction; False means already handled."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO processed_requests (request_id, handled_at) VALUES (?, ?)",
        (request_id, int(time.time())),
    )
    return cur.rowcount == 1


def begin_idempotent(conn, request_id):
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
    """`spec` is `"all"` or a digit string; reading the balance for "all" only inside an open
    transaction keeps two concurrent all-in bets from resolving the same stake."""
    if spec == "all":
        return get_balance(conn, user_id)
    return int(spec)


def try_debit(conn, user_id, amount):
    """Atomic subtract-if-covered; the check and the write are one statement."""
    ensure_account(conn, user_id)
    cur = conn.execute(
        "UPDATE accounts SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, user_id, amount),
    )
    return cur.rowcount == 1


def credit(conn, user_id, amount):
    ensure_account(conn, user_id)
    conn.execute("UPDATE accounts SET balance = balance + ? WHERE user_id = ?", (amount, user_id))


# --- cards ---


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
    aces = sum(1 for c in cards if c.startswith("A"))
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


def parse_amount_spec(spec):
    if spec.lower() == "all":
        return "all"
    if spec.isdigit():
        return spec
    raise BadArgument("amount must be a whole number of chips, or `all`")


def normalize_guess(raw):
    raw = raw.lower()
    if raw in ("heads", "h"):
        return "heads"
    if raw in ("tails", "t"):
        return "tails"
    raise BadArgument("call `heads`/`h` or `tails`/`t`")
