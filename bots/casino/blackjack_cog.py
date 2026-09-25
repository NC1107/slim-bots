"""bot-casino's blackjack commands - deal/hit/stand/double/split/surrender; an extension, see docs/framework.md."""

from slimbots import Embed

import blackjack
import casino_core


def _resolve_natural(player_natural, dealer_natural, amount):
    """3:2 on a natural blackjack, a push if the dealer has one too."""
    if player_natural and dealer_natural:
        return "push - you both had blackjack", amount
    if player_natural:
        payout = amount + (amount * 3) // 2
        return f"blackjack! +{payout - amount} chips", payout
    return "dealer has blackjack - you lose", 0


def _txn_deal_blackjack(conn, request_id, channel_id, user_id, amount_spec):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    if blackjack.has_round(conn, channel_id, user_id):
        conn.execute("ROLLBACK")
        return {"error": "finish your hand first - `!hit`, `!stand`, `!double`, `!split` or `!surrender`"}
    amount = casino_core.resolve_amount(conn, user_id, amount_spec)
    if amount < 1:
        conn.execute("ROLLBACK")
        return {"error": "you have no chips to bet - `!daily` first"}
    if amount > casino_core.MAX_AMOUNT:
        conn.execute("ROLLBACK")
        return {"error": f"keep a single bet under {casino_core.MAX_AMOUNT} chips"}
    if not casino_core.try_debit(conn, user_id, amount):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    player = [casino_core.draw_card(), casino_core.draw_card()]
    dealer = [casino_core.draw_card(), casino_core.draw_card()]
    player_natural = casino_core.hand_total(player) == 21
    dealer_natural = casino_core.hand_total(dealer) == 21
    if player_natural or dealer_natural:
        outcome, payout = _resolve_natural(player_natural, dealer_natural, amount)
        if payout:
            casino_core.credit(conn, user_id, payout)
        conn.execute("COMMIT")
        return {
            "resolved": True, "player": player, "dealer": dealer,
            "outcome": outcome, "balance": casino_core.get_balance(conn, user_id),
        }
    blackjack.start_round(conn, channel_id, user_id, amount, player, dealer)
    conn.execute("COMMIT")
    return {"resolved": False, "player": player, "dealer": dealer}


def _active_hand_or_none(conn, channel_id, user_id):
    hand = blackjack.active_hand(conn, channel_id, user_id)
    if hand is None:
        conn.execute("ROLLBACK")
    return hand


def _play_dealer_hand(dealer_cards):
    while casino_core.hand_total(dealer_cards) < 17:
        dealer_cards.append(casino_core.draw_card())
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
    dealer_total = casino_core.hand_total(dealer_cards)

    lines = []
    for idx, stake, player_cards, _, status in rounds:
        label = f"hand {idx + 1}: " if multi else "you: "
        if status == blackjack.BUST:
            lines.append(f"{label}{casino_core.render_hand(player_cards)} - bust")
        elif status == blackjack.SURRENDER:
            lines.append(f"{label}{casino_core.render_hand(player_cards)} - surrendered, {stake // 2} chips back")
        else:
            outcome, payout = _resolve_vs_dealer(stake, casino_core.hand_total(player_cards), dealer_total)
            if payout:
                casino_core.credit(conn, user_id, payout)
            lines.append(f"{label}{casino_core.render_hand(player_cards)} - {outcome}")
    if needs_dealer:
        lines.append(f"dealer: {casino_core.render_hand(dealer_cards)}")
    blackjack.clear_round(conn, channel_id, user_id)
    conn.execute("COMMIT")
    return {"next": False, "lines": lines, "balance": casino_core.get_balance(conn, user_id)}


def _txn_hit(conn, request_id, channel_id, user_id):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, _, player, _ = hand
    player.append(casino_core.draw_card())
    if casino_core.hand_total(player) > 21:
        blackjack.update_hand(conn, channel_id, user_id, hand_index, player_cards=player, status=blackjack.BUST)
        return {"finish": _finish_hand_result(conn, channel_id, user_id)}
    blackjack.update_hand(conn, channel_id, user_id, hand_index, player_cards=player)
    conn.execute("COMMIT")
    return {"player": player}


def _txn_stand(conn, request_id, channel_id, user_id):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, _, _, _ = hand
    blackjack.update_hand(conn, channel_id, user_id, hand_index, status=blackjack.STOOD)
    return {"finish": _finish_hand_result(conn, channel_id, user_id)}


def _txn_double(conn, request_id, channel_id, user_id):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, stake, player, _ = hand
    if not blackjack.can_double(player):
        conn.execute("ROLLBACK")
        return {"error": "you can only double on your first two cards"}
    if not casino_core.try_debit(conn, user_id, stake):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    player.append(casino_core.draw_card())
    status = blackjack.BUST if casino_core.hand_total(player) > 21 else blackjack.STOOD
    blackjack.update_hand(conn, channel_id, user_id, hand_index, player_cards=player, stake=stake * 2, status=status)
    return {"finish": _finish_hand_result(conn, channel_id, user_id)}


def _txn_split(conn, request_id, channel_id, user_id):
    if not casino_core.begin_idempotent(conn, request_id):
        return None
    hand = _active_hand_or_none(conn, channel_id, user_id)
    if hand is None:
        return {"error": "you don't have a hand going - `!blackjack <amount>` to start one"}
    hand_index, stake, player, dealer = hand
    already_split = len(blackjack.round_hands(conn, channel_id, user_id)) > 1
    if not blackjack.can_split(hand_index, player, already_split, casino_core.card_value):
        conn.execute("ROLLBACK")
        return {"error": "that hand can't be split - two cards of the same value, and only once"}
    if not casino_core.try_debit(conn, user_id, stake):
        conn.execute("ROLLBACK")
        return {"error": "you don't have that many chips"}
    first = [player[0], casino_core.draw_card()]
    second = [player[1], casino_core.draw_card()]
    blackjack.update_hand(conn, channel_id, user_id, 0, player_cards=first)
    blackjack.insert_split_hand(conn, channel_id, user_id, 1, stake, second, dealer)
    conn.execute("COMMIT")
    return {"first": first, "second": second, "dealer": dealer}


def _txn_surrender(conn, request_id, channel_id, user_id):
    if not casino_core.begin_idempotent(conn, request_id):
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
        casino_core.credit(conn, user_id, refund)
    blackjack.update_hand(conn, channel_id, user_id, hand_index, status=blackjack.SURRENDER)
    return {"finish": _finish_hand_result(conn, channel_id, user_id)}


async def _reply_finish_hand(ctx, result):
    if result["next"]:
        await ctx.reply(f"hand {result['index'] + 1}: {casino_core.render_hand(result['player'])}\n`!hit`, `!stand`, `!double` or `!surrender` for this hand")
        return
    embed = Embed(title="Blackjack", description="\n".join(result["lines"]), footer=f"balance: {result['balance']}")
    await ctx.reply(embed=embed)


def setup(bot):
    @bot.command(name="blackjack", aliases=["bj"], help="Deal a hand, then hit/stand/double/split/surrender", usage="<amount|all>")
    async def deal_blackjack(ctx, amount_spec: str):
        amount_spec = casino_core.parse_amount_spec(amount_spec)
        result = await bot.store.run(_txn_deal_blackjack, ctx.message["id"], ctx.channel_id, ctx.author.id, amount_spec)
        if result is None:
            return
        if "error" in result:
            await ctx.reply(result["error"])
            return
        if result["resolved"]:
            embed = Embed(
                title="Blackjack",
                description=f"you: {casino_core.render_hand(result['player'])}\ndealer: {casino_core.render_hand(result['dealer'])}\n{result['outcome']}",
                footer=f"balance: {result['balance']}",
            )
            await ctx.reply(embed=embed)
            return
        await ctx.reply(f"you: {casino_core.render_hand(result['player'])}\ndealer: {result['dealer'][0]} ??\n`!hit`, `!stand`, `!double`, `!split` or `!surrender`")

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
        await ctx.reply(f"you: {casino_core.render_hand(result['player'])}\n`!hit` or `!stand`")

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
            f"split into two hands\nhand 1: {casino_core.render_hand(result['first'])}\nhand 2: {casino_core.render_hand(result['second'])}\n"
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
