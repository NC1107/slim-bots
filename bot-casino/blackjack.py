"""Blackjack hand storage and the multi-hand state machine: hit, stand,
double down, split, and surrender.

Kept out of `bot.py` so one game's state machine does not crowd out the
money primitives and the other games. `bot.py` still owns dealing (`draw_card`,
`hand_total`, `render_hand`) and the debit/credit primitives; this module
only tracks which hands exist for a round and what state each is in.

**Why hands, plural.** A split turns one bet into two independent hands
sharing the same dealer up-card, each with its own stake and its own
hit/stand/bust outcome, settled against one dealer hand played out once
after every player hand is done. `hand_index` is `0` for the original hand
and `1` for the hand a split produces; splitting a second time is not
offered (`can_split` refuses once `hand_index 1` already exists), so a round
is at most two hands.

**Why `status` instead of a boolean.** `bust` and `surrender` end a hand's
own accounting immediately, without needing the dealer's total at all - a
busted or surrendered hand is a settled loss the moment it happens. `stood`
is the only status still waiting on the dealer, once the dealer's hand is
played out after the whole round (every hand) is done. `active` is not yet
decided. Doubling does not get its own status: it plays out to exactly one
more card and then becomes `bust` or `stood` like any other hand, with
`stake` already updated to the doubled amount by the caller.
"""

import time

ACTIVE = "active"
BUST = "bust"
SURRENDER = "surrender"
STOOD = "stood"

TEN_VALUE_RANKS = {"10", "J", "Q", "K"}


def init_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hands (
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            hand_index INTEGER NOT NULL,
            stake INTEGER NOT NULL,
            player_cards TEXT NOT NULL,
            dealer_cards TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            started_at INTEGER NOT NULL,
            PRIMARY KEY (channel_id, user_id, hand_index)
        )
        """
    )


def has_round(conn, channel_id, user_id):
    row = conn.execute(
        "SELECT 1 FROM hands WHERE channel_id = ? AND user_id = ? LIMIT 1", (channel_id, user_id)
    ).fetchone()
    return row is not None


def start_round(conn, channel_id, user_id, stake, player_cards, dealer_cards):
    conn.execute(
        "INSERT INTO hands (channel_id, user_id, hand_index, stake, player_cards, dealer_cards, status, started_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?)",
        (channel_id, user_id, stake, ",".join(player_cards), ",".join(dealer_cards), ACTIVE, int(time.time())),
    )


def active_hand(conn, channel_id, user_id):
    """`(hand_index, stake, player_cards, dealer_cards)` for the lowest-index
    hand still open to action, or `None` once every hand this round has
    resolved to `bust`, `surrender`, or `stood`."""
    row = conn.execute(
        "SELECT hand_index, stake, player_cards, dealer_cards FROM hands "
        "WHERE channel_id = ? AND user_id = ? AND status = ? ORDER BY hand_index LIMIT 1",
        (channel_id, user_id, ACTIVE),
    ).fetchone()
    if row is None:
        return None
    hand_index, stake, player_raw, dealer_raw = row
    return hand_index, stake, player_raw.split(","), dealer_raw.split(",")


def update_hand(conn, channel_id, user_id, hand_index, *, player_cards=None, stake=None, status=None):
    sets, params = [], []
    if player_cards is not None:
        sets.append("player_cards = ?")
        params.append(",".join(player_cards))
    if stake is not None:
        sets.append("stake = ?")
        params.append(stake)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    params.extend([channel_id, user_id, hand_index])
    conn.execute(f"UPDATE hands SET {', '.join(sets)} WHERE channel_id = ? AND user_id = ? AND hand_index = ?", params)


def insert_split_hand(conn, channel_id, user_id, hand_index, stake, player_cards, dealer_cards):
    conn.execute(
        "INSERT INTO hands (channel_id, user_id, hand_index, stake, player_cards, dealer_cards, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (channel_id, user_id, hand_index, stake, ",".join(player_cards), ",".join(dealer_cards), ACTIVE, int(time.time())),
    )


def round_hands(conn, channel_id, user_id):
    """Every hand this round, in `hand_index` order, as
    `(hand_index, stake, player_cards, dealer_cards, status)`."""
    rows = conn.execute(
        "SELECT hand_index, stake, player_cards, dealer_cards, status FROM hands "
        "WHERE channel_id = ? AND user_id = ? ORDER BY hand_index",
        (channel_id, user_id),
    ).fetchall()
    return [(idx, stake, p.split(","), d.split(","), status) for idx, stake, p, d, status in rows]


def clear_round(conn, channel_id, user_id):
    conn.execute("DELETE FROM hands WHERE channel_id = ? AND user_id = ?", (channel_id, user_id))


def can_split(hand_index, player_cards, already_split, card_value):
    """A split is offered only on the original, untouched two-card hand -
    never on a hand a split already produced (`hand_index != 0`) and never a
    second time this round (`already_split`, meaning a hand at index 1
    already exists) - and only when both cards share a value. Splitting a
    pair of ten-value cards or a pair of fives is allowed - it is the
    player's stake to risk - even though a fixed strategy would decline
    both; see the README's house-edge section."""
    return (
        hand_index == 0
        and not already_split
        and len(player_cards) == 2
        and card_value(player_cards[0]) == card_value(player_cards[1])
    )


def can_double(player_cards):
    """Double down is a first-action option: exactly the two cards dealt,
    nothing hit yet."""
    return len(player_cards) == 2


def can_surrender(hand_index, player_cards, already_split):
    """Surrender is offered only on an untouched two-card hand, and only
    before any split - the same restriction real tables place on it, since
    a hand already split or hit has taken an action past the point a
    surrender is meant to cover."""
    return hand_index == 0 and not already_split and len(player_cards) == 2
