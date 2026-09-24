# bot-casino

A slim-m bot with a per-person chip balance, two games, transfers, and a
leaderboard.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid>,<channel-uuid> \
python3 bot.py
```

`SLIMM_CHANNELS` is a comma-separated list of channel ids; the bot only
watches and answers there. See "Getting a token" and "What your bot may do"
in `docs/bots/building-bots.md` for how to find channel ids and grant the
bot `SEND_MESSAGES`/`VIEW_CHANNEL`. State (balances, cursors, in-progress
blackjack hands) lives in a sqlite file next to the script, `casino.db` by
default (`SLIMM_DB_PATH` to move it).

## Commands

- `!daily` - claim your daily chips.
- `!balance` (`!bal`) - see your balance.
- `!give <amount> <username>` - send chips to someone by username.
- `!flip <amount|all> <heads|tails>` - a coinflip.
- `!blackjack <amount|all>` (`!bj`) - deal a hand.
- `!hit` / `!stand` - the two actions every hand always has.
- `!double` (`!dbl`) - double your stake and take exactly one more card,
  first two cards only.
- `!split` - split a pair into two independent hands, first two cards only,
  once per round.
- `!surrender` (`!surr`) - forfeit half your stake and end the hand, first
  two cards only, before any split.
- `!leaderboard` (`!top`) - the top 10 balances.
- `!help` - this list, from the bot itself.

Commands are rate-limited per person (a burst allowance, not a play-speed
cap - see "Safeguards" below).

## Where chips come from

`!daily` is the only unconditional source: a flat 500 chips, once every 20
hours per account. 20 hours rather than a strict calendar day so claiming a
little early one day does not push someone's clock later and later until
they are locked out at their usual time; it still caps at roughly once a
day. There is no starting bonus separate from this - a brand new account's
first `!daily` is the starting bonus, since nothing stops it firing
immediately.

Nothing else creates chips except a bet actually winning, and nothing
removes chips except a bet actually losing or a transfer moving them to
someone else.

## The games and their expected value

Both games are honest about existing to be played, not to be beaten. The
numbers below are the actual house edge, not a marketing rounding of it.

**Coinflip (`!flip`).** Call heads or tails against a fair coin
(`secrets.choice`, not `random`, so it is not seedable). A win pays 1.9x the
stake, floored to a whole chip; a loss costs the stake. Expected value per
chip staked:

```
0.5 * (0.9) + 0.5 * (-1) = -0.05
```

A flat 5% house edge, by construction, before the floor rounding. The floor
only ever rounds down, i.e. only ever in the house's favor, and only
matters at all below a 10-chip stake.

**Blackjack (`!blackjack`, `!hit`, `!stand`, `!double`, `!split`,
`!surrender`).** An infinite shoe (every card is drawn independently and
uniformly from the 52 rank/suit combinations, so nothing is tracked or
countable), dealer stands on all 17s including soft 17, blackjack pays 3:2,
push on a tie. Double down, one split per round (no resplitting), and
surrender are all offered; insurance deliberately is not - see "What this
deliberately does not do." This is a real decision game rather than a
coinflip with cards, so there is no single clean formula the way there is
for `!flip` - the number below is a simulation, not a derivation.

Simulated over 3,000,000 rounds with a fixed strategy: surrender a hard 15
or 16 against a dealer 9, 10, or ace; otherwise split any pair except a pair
of fives or a pair of ten-value cards; otherwise double a hard 9, 10, or 11
on the first two cards; otherwise hit any hard total below 17, hit any soft
total below 18, otherwise stand - the same hit/stand rule as before, now
with the three new options layered in front of it, including on each half
of a split hand. Result: about **-3.9%** per chip actually staked (a split
or a double changes how many chips are at risk in a round, so the edge is
measured against that, not a flat per-round amount). That is a real
improvement over the **-5.5%** the single-hand game had - exactly what
adding player-favorable options should do - and it is still worse than a
full-rules casino table's roughly -0.5%, both because this fixed strategy is
not claimed optimal and because a few real-table options (late surrender
against every dealer card, resplitting, doubling after a split on more
totals) still are not offered. A player who plays better than this fixed
strategy - taking the dealer's visible card into account more precisely -
will do better than -3.9%; nothing here stops them from trying. The
simulation script is not included; rerunning it is a matter of importing
`bot.py`'s own `draw_card`, `hand_total`, `card_value`, `_resolve_natural`
and `_resolve_vs_dealer` and playing the strategy above against them, the
same way the original single-hand simulation did.

## Where the house edge goes

Nowhere. There is no house account. A win credits the account more chips
than were staked; a loss just discards what was staked, and a push
refunds it. Nobody's balance is the source of a win or the destination of
a loss.

What keeps the total chip supply from wandering off under ordinary play is
that both games have negative expected value, so on average more chips are
destroyed by losses than are created by wins. This is also the actual
anti-farming control: even a *fair*, zero-edge coin flip has zero expected
value and would not mint money on average either, but stating a real edge
is the honest version of the same guarantee, and it is what stops `!flip
all` from being a way to grind the leaderboard rather than gamble on it.
`!daily` is still the only reliable way for the total supply to grow.

## Concurrency: how bets are made safe, and how that was tested

Every command that touches a balance runs inside a `BEGIN IMMEDIATE` sqlite
transaction. `BEGIN IMMEDIATE` takes sqlite's write lock at the start of the
transaction rather than on the first write, so a second writer's own
`BEGIN IMMEDIATE` blocks until the first one commits or rolls back - there
is no window where two transactions can interleave their reads and writes.
`!flip all` and `!blackjack all` resolve "all" to a balance *inside* that
transaction, and every debit is one conditional statement,
`UPDATE accounts SET balance = balance - ? WHERE balance >= ?`, whose row
count says whether it actually happened. There is never a separate
read-the-balance step followed by a later write step for a second command
to land in between.

On top of that, every money-moving command first does
`INSERT OR IGNORE INTO processed_requests` keyed on the triggering
message's own id, in the same transaction as the balance change, and skips
the command entirely if that row already existed. This exists for a
narrower case than the transaction above: if the bot process crashes after
committing a bet's balance change but before its `seq` cursor write lands,
the next connection's `/sync` call replays that same message. Without the
guard it would be charged twice on a machine that merely restarted at a bad
moment, not on any actual double-send.

None of this was taken on faith. `test_concurrency.py` opens several
genuinely separate sqlite connections on real OS threads against the same
database file and hammers one account from all of them at once:

- fifty concurrent `!flip all` attempts against one balance - exactly one
  may win the race, and the balance never goes negative
- two concurrent transfers that together ask for more than the sender has
  - exactly one may succeed, and the sender-plus-both-recipients total is
  unchanged
- ten concurrent deliveries of the same request id - applied exactly once
- a mixed random workload of flips and gives across five accounts from
  eight threads at once, checked afterward against the one invariant that
  must always hold: total chips in play equals daily claims plus wins minus
  losses, exactly, with nothing lost or gained to a race

While building it, deliberately swapping the debit for a naive
read-then-sleep-then-write version *without* the transaction wrapper
reliably produced a large negative balance in the same scenario (tens of
thousands of chips overdrawn from fifty threads racing one account), which
is what confirms the test would actually have caught the bug it exists to
catch, not just exercised code that happened to pass.

Run it directly:

```bash
python3 test_concurrency.py
```

## Identity

Every balance is keyed on `author_id`, the message author's user id, never
on a display name - display names are not unique and can be changed.
`!give` resolves its target by username instead, since usernames (unlike
display names) are unique and stable; it walks `/members` into a
short-lived cache rather than requiring the recipient to have posted
recently.

## Safeguards

- **Bot and webhook authors are always ignored**, not just the bot's own
  messages - `slimbots.AuthorFilter`. This is the fix for a real misfire: a
  reminder whose text was `!daily` used to make this bot credit the
  *reminders bot's own account*, because balances were keyed on whoever
  posted the message without checking whether that "whoever" was a program.
- **A per-user command rate limit** (a burst allowance, not a play-speed
  cap - nobody typing commands by hand comes close to it) so a script
  hammering the bot gets a "slow down" reply, never a silent drop and never
  free reign to flood the channel or the database.
- **`!give` refuses a bot or webhook recipient.** Bots don't play, so a
  transfer to one is never a legitimate gift - almost always a typo or a
  confused command, and closing it costs nothing a real transfer needs.
- **A sane upper bound on any single bet or transfer** (`MAX_AMOUNT`, far
  above anything a real balance reaches), to keep a typo or a script from
  handing sqlite an integer past its 64-bit ceiling. This is not a betting
  limit in the "responsible gambling" sense - see below for why the bot
  still does not have one of those.
- **`processed_requests` is pruned** on an hourly sweep, deleting rows older
  than 30 days. The row only needs to survive one reconnect gap; without
  this it grew by one row per money-moving command forever.
- **Graceful shutdown and connection isolation** - `slimbots.lifecycle`.
  SIGTERM (docker sends it on every redeploy) exits cleanly, and a single
  bad message (a 403 from losing channel access, a bug) is caught and
  logged rather than tearing down the whole websocket connection.

## Where a safeguard was deliberately not added

- **Betting limits.** There is still no minimum or maximum bet beyond "at
  least 1 chip," "no more than you have," and the sanity ceiling above. The
  house edge is proportional to the stake by construction - a 10,000-chip
  bet loses the same 3.9% on average as a 10-chip one - so a max bet would
  not be closing a real weakness, only capping how big a swing someone can
  choose to take on a bet whose odds are already honestly stated. A
  community that wants a responsible-gambling style cap on top of that is
  welcome to add one; it is a policy choice this template does not make for
  them.
- **A transfer fee or an alt-farming block.** `!give` still moves chips
  one-for-one, and the rate limit above is friction against a script doing
  it fast, not a cap on doing it at all - a fee would tax every legitimate
  gift exactly as much as it slows down farming, and this deployment is
  still assumed to be a small, roughly trusted community where that tradeoff
  is not worth it.
- **Insurance in blackjack.** Double down, split, and surrender are all
  offered now; insurance deliberately is not. It is a side bet, separate
  from the hand's own outcome, that is negative-EV for the player under
  basic strategy in every case except card counting - and this shoe is
  drawn independently every hand, so there is nothing to count. Offering it
  would not be closing a weakness; it would be adding a way to talk someone
  into a worse bet than the ones already on offer.
- **A house-funded jackpot or progressive prize.** Every payout still comes
  from chips created or destroyed at the moment of that one bet's
  resolution; nothing accumulates anywhere to pay out later.
- **Recovering a very long outage exactly.** Cursor bootstrap and `/sync`
  cover ordinary reconnects. If a channel's cursor falls outside what
  `/sync` can answer (`reset: true`), this bot re-baselines at the
  channel's current head rather than reconstructing the exact gap - the
  same tradeoff `bot-reminders` accepts, and decision 0009's own
  "eventually consistent, resynchronize forward" position. A command sent
  inside that specific window is the one case this bot can miss.
- **Answering outside `SLIMM_CHANNELS`.** Every other channel the bot's
  role can see is left alone.
- **Resplitting, or splitting more than once per round.** `!split` is
  offered only on the original, untouched two-card hand. A hand a split
  already produced cannot be split again, matching what the bot's own state
  machine allows rather than a real table's fuller (and more stateful)
  resplitting rules.

## Output and embeds

Every reply is still plain text, formatted with markdown (bold, monospace
for cards and amounts, multi-line layout for a multi-hand round) rather than
raw sentences. The seam for a future embeds-aware version is already the
shape the code is in: each `handle_*` function builds its whole reply as one
string - `reply = f"..."` or the `lines`/`"\n".join(...)` pattern in
`_finish_hand` and `_reply_with_pending` - immediately before the single
`client.send(...)` call that ships it. Adopting embeds later means replacing
what that one string-building step produces with an embed payload, in each
handler, without touching the game logic above it or the dispatch in
`handle_message` at all.
