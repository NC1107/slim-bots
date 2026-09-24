"""Blackjack hand storage and the multi-hand state machine (hit/stand/double/split/surrender); see README.md."""

import time

ACTIVE = "active"
BUST = "bust"
SURRENDER = "surrender"
STOOD = "stood"


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
    """`(hand_index, stake, player_cards, dealer_cards)` for the lowest-index hand still open, or None."""
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
    """Every hand this round, in `hand_index` order, as `(hand_index, stake, player_cards, dealer_cards, status)`."""
    rows = conn.execute(
        "SELECT hand_index, stake, player_cards, dealer_cards, status FROM hands "
        "WHERE channel_id = ? AND user_id = ? ORDER BY hand_index",
        (channel_id, user_id),
    ).fetchall()
    return [(idx, stake, p.split(","), d.split(","), status) for idx, stake, p, d, status in rows]


def clear_round(conn, channel_id, user_id):
    conn.execute("DELETE FROM hands WHERE channel_id = ? AND user_id = ?", (channel_id, user_id))


def can_split(hand_index, player_cards, already_split, card_value):
    """The original two-card hand only, never already-split, and only a matching pair; see README.md."""
    return (
        hand_index == 0
        and not already_split
        and len(player_cards) == 2
        and card_value(player_cards[0]) == card_value(player_cards[1])
    )


def can_double(player_cards):
    """First action only: exactly the two cards dealt, nothing hit yet."""
    return len(player_cards) == 2


def can_surrender(hand_index, player_cards, already_split):
    """First action only, and never after a split."""
    return hand_index == 0 and not already_split and len(player_cards) == 2
