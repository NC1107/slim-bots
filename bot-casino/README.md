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
- `!blackjack <amount|all>` (`!bj`) - deal a hand, then `!hit` or `!stand`.
- `!leaderboard` (`!top`) - the top 10 balances.
- `!help` - this list, from the bot itself.

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

**Blackjack (`!blackjack`, `!hit`, `!stand`).** An infinite shoe (every
card is drawn independently and uniformly from the 52 rank/suit
combinations, so nothing is tracked or countable), dealer stands on all
17s including soft 17, blackjack pays 3:2, push on a tie, no double down,
no split, no insurance, no surrender. This is a real decision game rather
than a coinflip with cards, so there is no single clean formula the way
there is for `!flip` - the number below is a simulation, not a derivation.

Simulated over 3,000,000 hands with a fixed strategy (hit on any hard total
below 17, hit on any soft total below 18, otherwise stand): about **-5.5%**
per chip staked. That is worse than a full-rules casino table, where the
same shoe assumptions land closer to -0.5%, because double down and
splitting are both player-favorable moves this bot does not offer. A
player who deviates from that fixed threshold strategy - standing on a
stiff 12 against a dealer's weak upcard, say - will do a little better or
worse than -5.5%, since the dealer's visible card is genuinely informative
and this bot does not stop anyone from using it. The simulation script is
not included; rerunning it is a matter of copying `hand_total` and dealing
loop out of `bot.py` and playing the fixed strategy against itself.

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

## What this deliberately does not do

- **Betting limits.** There is no minimum or maximum bet beyond "at least 1
  chip" and "no more than you have." A community that wants a max bet, a
  cooldown between bets, or a daily loss cap would need to add one - this
  is a template, not a responsible-gambling framework.
- **A house-funded jackpot or progressive prize.** Every payout comes from
  chips created or destroyed at the moment of that one bet's resolution;
  nothing accumulates anywhere to pay out later.
- **Doubling down, splitting, insurance, or surrender in blackjack.**
  Leaving them out is most of why this bot's blackjack edge is worse than
  a real table's - see the expected value section. Adding them means
  tracking a hand as more than two card lists and a stake, and widening
  `!hit`/`!stand` into a small state machine with more than two states.
- **A transfer fee or cooldown.** `!give` moves chips one-for-one with no
  cut and no rate limit beyond the platform's own. Nothing stops someone
  farming `!daily` on an alt and feeding a main account; this deployment is
  assumed to be a small, roughly trusted community, and a fee big enough to
  matter against abuse would also tax every legitimate gift.
- **Pruning `processed_requests`.** It grows by one row per money-moving
  command, forever. The row only needs to survive one reconnect gap, so a
  real long-lived deployment would want a periodic `DELETE FROM
  processed_requests WHERE handled_at < ?` for anything more than a few
  days old; a template bot is not the place to also design that job's own
  schedule and locking.
- **Recovering a very long outage exactly.** Cursor bootstrap and `/sync`
  cover ordinary reconnects. If a channel's cursor falls outside what
  `/sync` can answer (`reset: true`), this bot re-baselines at the
  channel's current head rather than reconstructing the exact gap - the
  same tradeoff `bot-reminders` accepts, and decision 0009's own
  "eventually consistent, resynchronize forward" position. A command sent
  inside that specific window is the one case this bot can miss.
- **Answering outside `SLIMM_CHANNELS`.** Every other channel the bot's
  role can see is left alone.
- **Multiple simultaneous blackjack hands per person per channel.** One at
  a time; `!blackjack` refuses to deal a new hand until the open one is
  finished with `!hit` or `!stand`. A different channel is a different
  hand, since the state is keyed on `(channel_id, user_id)`.
