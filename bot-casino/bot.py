#!/usr/bin/env python3
"""bot-casino: chip balance, coinflip, and blackjack (hit/stand/double/split/surrender); see README.md."""

import asyncio
import secrets
import time

from slimbots import BadArgument, Bot, Embed, Member, RateLimiter

import blackjack

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
    """A real, synchronous connection - only `test_concurrency.py` uses this now; the bot goes through `bot.store`."""
    import sqlite3

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


bot = Bot(prefix="!", require_channels=True, default_data_path="casino.db", store_migrate=init_db)
_command_limiter = RateLimiter(COMMANDS_PER_WINDOW, COMMAND_WINDOW_SECONDS)


@bot.check
async def rate_limit(ctx):
    """A burst allowance against a script, not a play-speed cap on a person; see README.md."""
    return _command_limiter.check(ctx.author.id)


# --- transaction bodies: run on the store's worker thread, never on the event loop ---


def _txn_daily(conn, request_id, user_id):
    if not begin_idempotent(conn, request_id):
        return None
    ensure_account(conn, user_id)
    balance_, last_daily = conn.execute(
        "SELECT balance, last_daily FROM accounts WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = int(time.time())
    remaining = DAILY_COOLDOWN_SECONDS - (now - last_daily)
    if remaining > 0:
        conn.execute("ROLLBACK")
        return {"error": f"already claimed - try again in {format_duration(remaining)}"}
    conn.execute(
        "UPDATE accounts SET balance = balance + ?, last_daily = ? WHERE user_id = ?",
        (DAILY_AMOUNT, now, user_id),
    )
    conn.execute("COMMIT")
    return {"balance": balance_ + DAILY_AMOUNT}


def _txn_give(conn, request_id, sender_id, recipient_id, amount):
    if not begin_idempotent(conn, request_id):
        return None
    ensure_account(conn, recipient_id)
    if not try_debit(conn, sender_id, amount):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    credit(conn, recipient_id, amount)
    conn.execute("COMMIT")
    return {"balance": get_balance(conn, sender_id)}


def _txn_flip(conn, request_id, user_id, amount_spec, guess):
    if not begin_idempotent(conn, request_id):
        return None
    amount = resolve_amount(conn, user_id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        return {"error": "you have no chips to flip - `!daily` first"}
    if amount > MAX_AMOUNT:
        conn.execute("ROLLBACK")
        return {"error": f"keep a single flip under {MAX_AMOUNT} chips"}
    if not try_debit(conn, user_id, amount):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    landed = secrets.choice(("heads", "tails"))
    payout = (amount * FLIP_PAYOUT_NUM) // FLIP_PAYOUT_DEN if landed == guess else 0
    if payout:
        credit(conn, user_id, payout)
    conn.execute("COMMIT")
    return {"landed": landed, "amount": amount, "payout": payout, "balance": get_balance(conn, user_id)}


def _resolve_natural(player_natural, dealer_natural, amount):
    """3:2 on a natural blackjack, a push if the dealer has one too."""
    if player_natural and dealer_natural:
        return "push - you both had blackjack", amount
    if player_natural:
        payout = amount + (amount * 3) // 2
        return f"blackjack! +{payout - amount} chips", payout
    return "dealer has blackjack - you lose", 0


def _txn_deal_blackjack(conn, request_id, channel_id, user_id, amount_spec):
    if not begin_idempotent(conn, request_id):
        return None
    if blackjack.has_round(conn, channel_id, user_id):
        conn.execute("ROLLBACK")
        return {"error": "finish your hand first - `!hit`, `!stand`, `!double`, `!split` or `!surrender`"}
    amount = resolve_amount(conn, user_id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        return {"error": "you have no chips to bet - `!daily` first"}
    if amount > MAX_AMOUNT:
        conn.execute("ROLLBACK")
        return {"error": f"keep a single bet under {MAX_AMOUNT} chips"}
    if not try_debit(conn, user_id, amount):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    player = [draw_card(), draw_card()]
    dealer = [draw_card(), draw_card()]
    player_natural = hand_total(player) == 21
    dealer_natural = hand_total(dealer) == 21
    if player_natural or dealer_natural:
        outcome, payout = _resolve_natural(player_natural, dealer_natural, amount)
        if payout:
            credit(conn, user_id, payout)
        conn.execute("COMMIT")
        return {"resolved": True, "player": player, "dealer": dealer, "outcome": outcome, "balance": get_balance(conn, user_id)}
    blackjack.start_round(conn, channel_id, user_id, amount, player, dealer)
    conn.execute("COMMIT")
    return {"resolved": False, "player": player, "dealer": dealer}


def _active_hand_or_none(conn, channel_id, user_id):
    hand = blackjack.active_hand(conn, channel_id, user_id)
    if hand is None:
        conn.execute("ROLLBACK")
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


def _finish_hand_result(conn, channel_id, user_id):
    """Points at the next split hand still waiting, or settles the whole round against the dealer once every hand is decided."""
    next_hand = blackjack.active_hand(conn, channel_id, user_id)
    if next_hand is not None:
        next_index, _, next_player, _ = next_hand
        conn.execute("COMMIT")
        return {"next": True, "index": next_index, "player": next_player}

    rounds = blackjack.round_hands(conn, channel_id, user_id)
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
                credit(conn, user_id, payout)
            lines.append(f"{label}{render_hand(player_cards)} - {outcome}")
    if needs_dealer:
        lines.append(f"dealer: {render_hand(dealer_cards)}")
    blackjack.clear_round(conn, channel_id, user_id)
    conn.execute("COMMIT")
    return {"next": False, "lines": lines, "balance": get_balance(conn, user_id)}


def _txn_hit(conn, request_id, channel_id, user_id):
    if not begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, _, player, _ = hand
    player.append(draw_card())
    if hand_total(player) > 21:
        blackjack.update_hand(conn, channel_id, user_id, hand_index, player_cards=player, status=blackjack.BUST)
        return {"finish": _finish_hand_result(conn, channel_id, user_id)}
    blackjack.update_hand(conn, channel_id, user_id, hand_index, player_cards=player)
    conn.execute("COMMIT")
    return {"player": player}


def _txn_stand(conn, request_id, channel_id, user_id):
    if not begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, _, _, _ = hand
    blackjack.update_hand(conn, channel_id, user_id, hand_index, status=blackjack.STOOD)
    return {"finish": _finish_hand_result(conn, channel_id, user_id)}


def _txn_double(conn, request_id, channel_id, user_id):
    if not begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, stake, player, _ = hand
    if not blackjack.can_double(player):
        conn.execute("ROLLBACK")
        return {"error": "you can only double on your first two cards"}
    if not try_debit(conn, user_id, stake):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    player.append(draw_card())
    status = blackjack.BUST if hand_total(player) > 21 else blackjack.STOOD
    blackjack.update_hand(conn, channel_id, user_id, hand_index, player_cards=player, stake=stake * 2, status=status)
    return {"finish": _finish_hand_result(conn, channel_id, user_id)}


def _txn_split(conn, request_id, channel_id, user_id):
    if not begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, stake, player, dealer = hand
    already_split = len(blackjack.round_hands(conn, channel_id, user_id)) > 1
    if not blackjack.can_split(hand_index, player, already_split, card_value):
        conn.execute("ROLLBACK")
        return {"error": "that hand can't be split - two cards of the same value, and only once"}
    if not try_debit(conn, user_id, stake):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    first = [player[0], draw_card()]
    second = [player[1], draw_card()]
    blackjack.update_hand(conn, channel_id, user_id, 0, player_cards=first)
    blackjack.insert_split_hand(conn, channel_id, user_id, 1, stake, second, dealer)
    conn.execute("COMMIT")
    return {"first": first, "second": second, "dealer": dealer}


def _txn_surrender(conn, request_id, channel_id, user_id):
    if not begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, stake, player, _ = hand
    already_split = len(blackjack.round_hands(conn, channel_id, user_id)) > 1
    if not blackjack.can_surrender(hand_index, player, already_split):
        conn.execute("ROLLBACK")
        return {"error": "surrender is only offered on your first two cards, before any split"}
    refund = stake // 2
    if refund:
        credit(conn, user_id, refund)
    blackjack.update_hand(conn, channel_id, user_id, hand_index, status=blackjack.SURRENDER)
    return {"finish": _finish_hand_result(conn, channel_id, user_id)}


async def _reply_finish_hand(ctx, result):
    if result["next"]:
        await ctx.reply(f"hand {result['index'] + 1}: {render_hand(result['player'])}\n`!hit`, `!stand`, `!double` or `!surrender` for this hand")
        return
    embed = Embed(title="Blackjack", description="\n".join(result["lines"]), footer=f"balance: {result['balance']}")
    await ctx.reply(embed=embed)


# --- commands ---


@bot.command(aliases=["bal"], help="See your balance")
async def balance(ctx):
    bal = await bot.store.run(get_balance, ctx.author.id)
    embed = Embed(title=ctx.author.display_name).add_field("chips", str(bal))
    await ctx.reply(embed=embed)


@bot.command(help="Claim your daily chips")
async def daily(ctx):
    result = await bot.store.run(_txn_daily, ctx.message["id"], ctx.author.id)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    await ctx.reply(f"claimed {DAILY_AMOUNT} chips. balance: {result['balance']}")


@bot.command(aliases=["top"], help="Top 10 balances")
async def leaderboard(ctx):
    rows = await bot.store.run(
        lambda conn: conn.execute(
            "SELECT user_id, balance FROM accounts WHERE balance > 0 ORDER BY balance DESC LIMIT 10"
        ).fetchall()
    )
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
    result = await bot.store.run(_txn_give, ctx.message["id"], ctx.author.id, member.id, amount)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    await ctx.reply(f"sent {amount} chips to {member.display_name}. your balance: {result['balance']}")


@bot.command(help="Coinflip, about a 5% house edge", usage="<amount|all> <heads|tails>")
async def flip(ctx, amount_spec: str, guess: str):
    amount_spec = _parse_amount_spec(amount_spec)
    guess = _normalize_guess(guess)
    result = await bot.store.run(_txn_flip, ctx.message["id"], ctx.author.id, amount_spec, guess)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    landed, amount, payout, bal = result["landed"], result["amount"], result["payout"], result["balance"]
    if payout:
        await ctx.reply(f"the coin lands on {landed} - you called it, +{payout - amount} chips. balance: {bal}")
    else:
        await ctx.reply(f"the coin lands on {landed} - you called {guess}, -{amount} chips. balance: {bal}")


@bot.command(name="blackjack", aliases=["bj"], help="Deal a hand, then hit/stand/double/split/surrender", usage="<amount|all>")
async def deal_blackjack(ctx, amount_spec: str):
    amount_spec = _parse_amount_spec(amount_spec)
    result = await bot.store.run(_txn_deal_blackjack, ctx.message["id"], ctx.channel_id, ctx.author.id, amount_spec)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    if result["resolved"]:
        embed = Embed(
            title="Blackjack",
            description=f"you: {render_hand(result['player'])}\ndealer: {render_hand(result['dealer'])}\n{result['outcome']}",
            footer=f"balance: {result['balance']}",
        )
        await ctx.reply(embed=embed)
        return
    await ctx.reply(f"you: {render_hand(result['player'])}\ndealer: {result['dealer'][0]} ??\n`!hit`, `!stand`, `!double`, `!split` or `!surrender`")


@bot.command(help="Take another card")
async def hit(ctx):
    result = await bot.store.run(_txn_hit, ctx.message["id"], ctx.channel_id, ctx.author.id)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    if "finish" in result:
        await _reply_finish_hand(ctx, result["finish"])
        return
    await ctx.reply(f"you: {render_hand(result['player'])}\n`!hit` or `!stand`")


@bot.command(help="Stop drawing and settle the hand")
async def stand(ctx):
    result = await bot.store.run(_txn_stand, ctx.message["id"], ctx.channel_id, ctx.author.id)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    await _reply_finish_hand(ctx, result["finish"])


@bot.command(aliases=["dbl"], help="Double your stake and take exactly one more card")
async def double(ctx):
    result = await bot.store.run(_txn_double, ctx.message["id"], ctx.channel_id, ctx.author.id)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    await _reply_finish_hand(ctx, result["finish"])


@bot.command(help="Split a pair into two independent hands")
async def split(ctx):
    result = await bot.store.run(_txn_split, ctx.message["id"], ctx.channel_id, ctx.author.id)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    await ctx.reply(
        f"split into two hands\nhand 1: {render_hand(result['first'])}\nhand 2: {render_hand(result['second'])}\n"
        f"dealer: {result['dealer'][0]} ??\nplaying hand 1 - `!hit`, `!stand` or `!double`"
    )


@bot.command(aliases=["surr"], help="Forfeit half your stake and end the hand")
async def surrender(ctx):
    result = await bot.store.run(_txn_surrender, ctx.message["id"], ctx.channel_id, ctx.author.id)
    if result is None:
        return
    if "error" in result:
        await ctx.reply(result["error"])
        return
    await _reply_finish_hand(ctx, result["finish"])


# --- connection lifecycle ---


_maintenance_started = False


@bot.event
async def on_ready():
    global _maintenance_started
    if _maintenance_started:
        return
    _maintenance_started = True
    bot.background(_maintenance(), name="casino-maintenance")


async def _maintenance():
    """Prunes processed_requests hourly; the row only needs to survive one reconnect gap."""
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        await bot.store.run(prune_processed_requests, int(time.time()) - PROCESSED_REQUEST_RETENTION_SECONDS)


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
