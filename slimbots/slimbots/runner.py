"""The reconnect loop every template but bot-ping needs: exponential backoff,
reset on a connection that actually completes its hello handshake, and a
revoked token (401) as the one failure that stops the loop instead of
retrying it.
"""

import asyncio
import sys

from .client import is_token_revoked


async def run_forever(attempt, *, base_delay=1.0, max_delay=60.0):
    """Calls `await attempt(reset_delay)` forever, backing off between
    failures.

    `attempt` does one connection's worth of work - typically resync, open a
    `Connection`, call `reset_delay()` once hello succeeds, then read frames
    until the socket drops or errors. Returns 1 once the token is found to be
    revoked, so `main()` can propagate it as the process exit code; runs
    until cancelled otherwise.
    """
    delay = base_delay

    def reset_delay():
        nonlocal delay
        delay = base_delay

    while True:
        try:
            await attempt(reset_delay)
        except Exception as err:
            if is_token_revoked(err):
                print("token rejected - revoked?", file=sys.stderr)
                return 1
            print(f"{type(err).__name__}: {err}, retrying in {delay}s", file=sys.stderr)
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)
