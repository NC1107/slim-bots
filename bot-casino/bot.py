#!/usr/bin/env python3
"""bot-casino: chip balance, coinflip, and blackjack (hit/stand/double/split/surrender); see README.md."""

import asyncio
import os
import secrets
import sqlite3
import time

from slimbots import BadArgument, Bot, Embed, Member, RateLimiter

import blackjack

DB_PATH = os.environ.get("SLIMM_DB_PATH", "casino.db")

DAILY_AMOUNT = 500
DAILY_COOLDOWN_SECONDS = 20 * 3600
FLIP_PAYOUT_NUM = 19  # win returns 1.9x the stake, floored to a whole chip
FLIP_PAYOUT_DEN = 10
MAX_AMOUNT = 1_000_000_000_000  # well under sqlite's 64-bit ceiling; see README.md
COMMANDS_PER_WINDOW = 12
COMMAND_WINDOW_SECONDS = 10
PROCESSED_REQUEST_RETENTION_SECONDS = 30 * 24 * 3600
PRUNE_INTERVAL_SECONDS = 3600

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["H", "D", "C", "S"]


# --- durable state; test_concurrency.py imports these by name, unchanged ---


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
    """`spec` is `"all"` or a digit string. Reading the balance for "all" only
    inside an open transaction keeps two concurrent all-in bets from resolving the same stake."""
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


async def debit_or_refuse(ctx, conn, amount):
    if try_debit(conn, ctx.author.id, amount):
        return True
    conn.execute("ROLLBACK")
    await ctx.reply("you don't have that many chips")
    return False


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


def _parse_amount_spec(spec):
    if spec.lower() == "all":
        return "all"
    if spec.isdigit():
        return spec
    raise BadArgument("amount must be a whole number of chips, or `all`")


def _normalize_guess(raw):
    raw = raw.lower()
    if raw in ("heads", "h"):
        return "heads"
    if raw in ("tails", "t"):
        return "tails"
    raise BadArgument("call `heads`/`h` or `tails`/`t`")


bot = Bot(prefix="!", require_channels=True, cursor_path=DB_PATH)
_command_limiter = RateLimiter(COMMANDS_PER_WINDOW, COMMAND_WINDOW_SECONDS)


@bot.check
async def rate_limit(ctx):
    """A burst allowance against a script, not a play-speed cap on a person; see README.md."""
    return _command_limiter.check(ctx.author.id)


@bot.command(aliases=["bal"], help="See your balance")
async def balance(ctx):
    embed = Embed(title=ctx.author.display_name).add_field("chips", str(get_balance(bot.db, ctx.author.id)))
    await ctx.reply(embed=embed)


@bot.command(help="Claim your daily chips")
async def daily(ctx):
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    ensure_account(conn, ctx.author.id)
    balance_, last_daily = conn.execute(
        "SELECT balance, last_daily FROM accounts WHERE user_id = ?", (ctx.author.id,)
    ).fetchone()
    now = int(time.time())
    remaining = DAILY_COOLDOWN_SECONDS - (now - last_daily)
    if remaining > 0:
        conn.execute("ROLLBACK")
        await ctx.reply(f"already claimed - try again in {format_duration(remaining)}")
        return
    conn.execute(
        "UPDATE accounts SET balance = balance + ?, last_daily = ? WHERE user_id = ?",
        (DAILY_AMOUNT, now, ctx.author.id),
    )
    conn.execute("COMMIT")
    await ctx.reply(f"claimed {DAILY_AMOUNT} chips. balance: {balance_ + DAILY_AMOUNT}")


@bot.command(aliases=["top"], help="Top 10 balances")
async def leaderboard(ctx):
    rows = bot.db.execute(
        "SELECT user_id, balance FROM accounts WHERE balance > 0 ORDER BY balance DESC LIMIT 10"
    ).fetchall()
    if not rows:
        await ctx.reply("nobody has any chips yet - `!daily` to start")
        return
    lines = ["chip leaderboard:"]
    for i, (user_id, bal) in enumerate(rows, 1):
        member = bot.space.members.get(user_id)
        name = member.display_name if member else user_id[:8]
        lines.append(f"{i}. {name} - {bal}")
    await ctx.reply("\n".join(lines))


@bot.command(help="Send chips to someone", usage="<amount> <username>")
async def give(ctx, amount: int, member: Member):
    if amount < 1:
        await ctx.reply("give at least 1 chip")
        return
    if amount > MAX_AMOUNT:
        await ctx.reply(f"keep a single transfer under {MAX_AMOUNT} chips")
        return
    if member.id == ctx.author.id:
        await ctx.reply("you can't send chips to yourself")
        return
    if member.is_bot or member.is_webhook:
        await ctx.reply("bots don't play, so they don't take chips either")
        return
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    ensure_account(conn, member.id)
    if not await debit_or_refuse(ctx, conn, amount):
        return
    credit(conn, member.id, amount)
    conn.execute("COMMIT")
    await ctx.reply(f"sent {amount} chips to {member.display_name}. your balance: {get_balance(conn, ctx.author.id)}")


@bot.command(help="Coinflip, about a 5% house edge", usage="<amount|all> <heads|tails>")
async def flip(ctx, amount_spec: str, guess: str):
    amount_spec = _parse_amount_spec(amount_spec)
    guess = _normalize_guess(guess)
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    amount = resolve_amount(conn, ctx.author.id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        await ctx.reply("you have no chips to flip - `!daily` first")
        return
    if amount > MAX_AMOUNT:
        conn.execute("ROLLBACK")
        await ctx.reply(f"keep a single flip under {MAX_AMOUNT} chips")
        return
    if not await debit_or_refuse(ctx, conn, amount):
        return
    landed = secrets.choice(("heads", "tails"))
    payout = (amount * FLIP_PAYOUT_NUM) // FLIP_PAYOUT_DEN if landed == guess else 0
    if payout:
        credit(conn, ctx.author.id, payout)
    conn.execute("COMMIT")
    bal = get_balance(conn, ctx.author.id)
    if payout:
        await ctx.reply(f"the coin lands on {landed} - you called it, +{payout - amount} chips. balance: {bal}")
    else:
        await ctx.reply(f"the coin lands on {landed} - you called {guess}, -{amount} chips. balance: {bal}")


def _resolve_natural(player_natural, dealer_natural, amount):
    """3:2 on a natural blackjack, a push if the dealer has one too."""
    if player_natural and dealer_natural:
        return "push - you both had blackjack", amount
    if player_natural:
        payout = amount + (amount * 3) // 2
        return f"blackjack! +{payout - amount} chips", payout
    return "dealer has blackjack - you lose", 0


@bot.command(name="blackjack", aliases=["bj"], help="Deal a hand, then hit/stand/double/split/surrender", usage="<amount|all>")
async def deal_blackjack(ctx, amount_spec: str):
    amount_spec = _parse_amount_spec(amount_spec)
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    if blackjack.has_round(conn, ctx.channel_id, ctx.author.id):
        conn.execute("ROLLBACK")
        await ctx.reply("finish your hand first - `!hit`, `!stand`, `!double`, `!split` or `!surrender`")
        return
    amount = resolve_amount(conn, ctx.author.id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        await ctx.reply("you have no chips to bet - `!daily` first")
        return
    if amount > MAX_AMOUNT:
        conn.execute("ROLLBACK")
        await ctx.reply(f"keep a single bet under {MAX_AMOUNT} chips")
        return
    if not await debit_or_refuse(ctx, conn, amount):
        return
    player = [draw_card(), draw_card()]
    dealer = [draw_card(), draw_card()]
    player_natural = hand_total(player) == 21
    dealer_natural = hand_total(dealer) == 21
    if player_natural or dealer_natural:
        outcome, payout = _resolve_natural(player_natural, dealer_natural, amount)
        if payout:
            credit(conn, ctx.author.id, payout)
        conn.execute("COMMIT")
        bal = get_balance(conn, ctx.author.id)
        embed = Embed(title="Blackjack", description=f"you: {render_hand(player)}\ndealer: {render_hand(dealer)}\n{outcome}", footer=f"balance: {bal}")
        await ctx.reply(embed=embed)
        return
    blackjack.start_round(conn, ctx.channel_id, ctx.author.id, amount, player, dealer)
    conn.execute("COMMIT")
    await ctx.reply(f"you: {render_hand(player)}\ndealer: {dealer[0]} ??\n`!hit`, `!stand`, `!double`, `!split` or `!surrender`")


async def _active_hand_or_refuse(ctx, conn):
    """The guard every mid-hand command (hit/stand/double/split/surrender) shares."""
    hand = blackjack.active_hand(conn, ctx.channel_id, ctx.author.id)
    if hand is None:
        conn.execute("ROLLBACK")
        await ctx.reply("you don't have a hand going - `!blackjack <amount>` to start one")
    return hand


def _play_dealer_hand(dealer_cards):
    while hand_total(dealer_cards) < 17:
        dealer_cards.append(draw_card())
    return dealer_cards


def _resolve_vs_dealer(stake, player_total, dealer_total):
    if dealer_total > 21 or dealer_total < player_total:
        return "win", stake * 2
    if dealer_total > player_total:
        return "dealer wins", 0
    return "push", stake


async def _finish_hand(ctx, conn):
    """Called once a hand is bust/surrendered/stood; points at the next split
    hand still waiting, or settles the whole round against the dealer once every hand is decided."""
    next_hand = blackjack.active_hand(conn, ctx.channel_id, ctx.author.id)
    if next_hand is not None:
        next_index, _, next_player, _ = next_hand
        conn.execute("COMMIT")
        await ctx.reply(f"hand {next_index + 1}: {render_hand(next_player)}\n`!hit`, `!stand`, `!double` or `!surrender` for this hand")
        return

    rounds = blackjack.round_hands(conn, ctx.channel_id, ctx.author.id)
    multi = len(rounds) > 1
    needs_dealer = any(status == blackjack.STOOD for _, _, _, _, status in rounds)
    dealer_cards = _play_dealer_hand(rounds[0][3]) if needs_dealer else rounds[0][3]
    dealer_total = hand_total(dealer_cards)

    lines = []
    for idx, stake, player_cards, _, status in rounds:
        label = f"hand {idx + 1}: " if multi else "you: "
        if status == blackjack.BUST:
            lines.append(f"{label}{render_hand(player_cards)} - bust")
        elif status == blackjack.SURRENDER:
            lines.append(f"{label}{render_hand(player_cards)} - surrendered, {stake // 2} chips back")
        else:
            outcome, payout = _resolve_vs_dealer(stake, hand_total(player_cards), dealer_total)
            if payout:
                credit(conn, ctx.author.id, payout)
            lines.append(f"{label}{render_hand(player_cards)} - {outcome}")
    if needs_dealer:
        lines.append(f"dealer: {render_hand(dealer_cards)}")
    blackjack.clear_round(conn, ctx.channel_id, ctx.author.id)
    conn.execute("COMMIT")
    embed = Embed(title="Blackjack", description="\n".join(lines), footer=f"balance: {get_balance(conn, ctx.author.id)}")
    await ctx.reply(embed=embed)


@bot.command(help="Take another card")
async def hit(ctx):
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    hand = await _active_hand_or_refuse(ctx, conn)
    if hand is None:
        return
    hand_index, _, player, _ = hand
    player.append(draw_card())
    if hand_total(player) > 21:
        blackjack.update_hand(conn, ctx.channel_id, ctx.author.id, hand_index, player_cards=player, status=blackjack.BUST)
        await _finish_hand(ctx, conn)
        return
    blackjack.update_hand(conn, ctx.channel_id, ctx.author.id, hand_index, player_cards=player)
    conn.execute("COMMIT")
    await ctx.reply(f"you: {render_hand(player)}\n`!hit` or `!stand`")


@bot.command(help="Stop drawing and settle the hand")
async def stand(ctx):
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    hand = await _active_hand_or_refuse(ctx, conn)
    if hand is None:
        return
    hand_index, _, _, _ = hand
    blackjack.update_hand(conn, ctx.channel_id, ctx.author.id, hand_index, status=blackjack.STOOD)
    await _finish_hand(ctx, conn)


@bot.command(aliases=["dbl"], help="Double your stake and take exactly one more card")
async def double(ctx):
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    hand = await _active_hand_or_refuse(ctx, conn)
    if hand is None:
        return
    hand_index, stake, player, _ = hand
    if not blackjack.can_double(player):
        conn.execute("ROLLBACK")
        await ctx.reply("you can only double on your first two cards")
        return
    if not await debit_or_refuse(ctx, conn, stake):
        return
    player.append(draw_card())
    status = blackjack.BUST if hand_total(player) > 21 else blackjack.STOOD
    blackjack.update_hand(conn, ctx.channel_id, ctx.author.id, hand_index, player_cards=player, stake=stake * 2, status=status)
    await _finish_hand(ctx, conn)


@bot.command(help="Split a pair into two independent hands")
async def split(ctx):
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    hand = await _active_hand_or_refuse(ctx, conn)
    if hand is None:
        return
    hand_index, stake, player, dealer = hand
    already_split = len(blackjack.round_hands(conn, ctx.channel_id, ctx.author.id)) > 1
    if not blackjack.can_split(hand_index, player, already_split, card_value):
        conn.execute("ROLLBACK")
        await ctx.reply("that hand can't be split - two cards of the same value, and only once")
        return
    if not await debit_or_refuse(ctx, conn, stake):
        return
    first = [player[0], draw_card()]
    second = [player[1], draw_card()]
    blackjack.update_hand(conn, ctx.channel_id, ctx.author.id, 0, player_cards=first)
    blackjack.insert_split_hand(conn, ctx.channel_id, ctx.author.id, 1, stake, second, dealer)
    conn.execute("COMMIT")
    await ctx.reply(
        f"split into two hands\nhand 1: {render_hand(first)}\nhand 2: {render_hand(second)}\n"
        f"dealer: {dealer[0]} ??\nplaying hand 1 - `!hit`, `!stand` or `!double`"
    )


@bot.command(aliases=["surr"], help="Forfeit half your stake and end the hand")
async def surrender(ctx):
    conn = bot.db
    if not begin_idempotent(conn, ctx.message["id"]):
        return
    hand = await _active_hand_or_refuse(ctx, conn)
    if hand is None:
        return
    hand_index, stake, player, _ = hand
    already_split = len(blackjack.round_hands(conn, ctx.channel_id, ctx.author.id)) > 1
    if not blackjack.can_surrender(hand_index, player, already_split):
        conn.execute("ROLLBACK")
        await ctx.reply("surrender is only offered on your first two cards, before any split")
        return
    refund = stake // 2
    if refund:
        credit(conn, ctx.author.id, refund)
    blackjack.update_hand(conn, ctx.channel_id, ctx.author.id, hand_index, status=blackjack.SURRENDER)
    await _finish_hand(ctx, conn)


# --- connection lifecycle ---


_maintenance_started = False


@bot.event
async def on_ready():
    global _maintenance_started
    if _maintenance_started:
        return
    _maintenance_started = True
    asyncio.create_task(_maintenance())


async def _maintenance():
    """Prunes processed_requests hourly; the row only needs to survive one reconnect gap."""
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        prune_processed_requests(bot.db, int(time.time()) - PROCESSED_REQUEST_RETENTION_SECONDS)


def main():
    bot.db = open_db(DB_PATH)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
