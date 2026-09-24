"""bot-casino's economy commands - balance/daily/leaderboard/give/flip; an extension, see docs/framework.md."""

import secrets
import time

from slimbots import Embed, Member

import casino_core


def _txn_daily(conn, request_id, user_id):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    casino_core.ensure_account(conn, user_id)
    balance_, last_daily = conn.execute(
        "SELECT balance, last_daily FROM accounts WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = int(time.time())
    remaining = casino_core.DAILY_COOLDOWN_SECONDS - (now - last_daily)
    if remaining > 0:
        conn.execute("ROLLBACK")
        return {"error": f"already claimed - try again in {casino_core.format_duration(remaining)}"}
    conn.execute(
        "UPDATE accounts SET balance = balance + ?, last_daily = ? WHERE user_id = ?",
        (casino_core.DAILY_AMOUNT, now, user_id),
    )
    conn.execute("COMMIT")
    return {"balance": balance_ + casino_core.DAILY_AMOUNT}


def _txn_give(conn, request_id, sender_id, recipient_id, amount):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    casino_core.ensure_account(conn, recipient_id)
    if not casino_core.try_debit(conn, sender_id, amount):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    casino_core.credit(conn, recipient_id, amount)
    conn.execute("COMMIT")
    return {"balance": casino_core.get_balance(conn, sender_id)}


def _txn_flip(conn, request_id, user_id, amount_spec, guess):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    amount = casino_core.resolve_amount(conn, user_id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        return {"error": "you have no chips to flip - `!daily` first"}
    if amount > casino_core.MAX_AMOUNT:
        conn.execute("ROLLBACK")
        return {"error": f"keep a single flip under {casino_core.MAX_AMOUNT} chips"}
    if not casino_core.try_debit(conn, user_id, amount):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    landed = secrets.choice(("heads", "tails"))
    payout = (amount * casino_core.FLIP_PAYOUT_NUM) // casino_core.FLIP_PAYOUT_DEN if landed == guess else 0
    if payout:
        casino_core.credit(conn, user_id, payout)
    conn.execute("COMMIT")
    return {"landed": landed, "amount": amount, "payout": payout, "balance": casino_core.get_balance(conn, user_id)}


def _fetch_leaderboard(conn):
    return conn.execute(
        "SELECT user_id, balance FROM accounts WHERE balance > 0 ORDER BY balance DESC LIMIT 10"
    ).fetchall()


def setup(bot):
    @bot.command(aliases=["bal"], help="See your balance")
    async def balance(ctx):
        bal = await bot.store.run(casino_core.get_balance, ctx.author.id)
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
        await ctx.reply(f"claimed {casino_core.DAILY_AMOUNT} chips. balance: {result['balance']}")

    @bot.command(aliases=["top"], help="Top 10 balances")
    async def leaderboard(ctx):
        rows = await bot.store.run(_fetch_leaderboard)
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
        if amount > casino_core.MAX_AMOUNT:
            await ctx.reply(f"keep a single transfer under {casino_core.MAX_AMOUNT} chips")
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
        amount_spec = casino_core.parse_amount_spec(amount_spec)
        guess = casino_core.normalize_guess(guess)
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
