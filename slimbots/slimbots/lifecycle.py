"""Process- and connection-level robustness: a clean exit on SIGTERM, and
keeping one bad message from tearing down a whole websocket connection.

`run_forever` (see `runner.py`) already gets the transport-level cases
right: a 401 stops the loop for good, anything else backs off and
reconnects. What it cannot fix on its own is a failure raised *inside* one
message handler mid-connection - a 403 because the bot lost access to a
channel, a transient 5xx from a single call, a bug tripped by one specific
message. Left alone that exception unwinds out of the frame-reading loop,
closes a socket that was otherwise fine, and forces a reconnect (and a fresh
`/sync`) over one bad message. `guard_handler` isolates that.
"""

import asyncio
import signal
import sys

from .client import is_token_revoked


async def run_with_shutdown(coro_factory):
    """Runs `coro_factory()` to completion, cancelling it cleanly on
    SIGTERM or SIGINT rather than leaving the default asyncio behavior - an
    unhandled-exception traceback wherever the signal happened to land.

    Docker sends SIGTERM to every container on `restart: unless-stopped` and
    on `docker compose up -d --build`, not only on a deliberate stop - every
    bot in this repo runs that way, so this matters on an ordinary redeploy,
    not just an intentional shutdown. Returns the coroutine's own result, or
    0 once our own signal handler is what cancelled it - a cancellation this
    function did not itself request is re-raised rather than swallowed,
    since absorbing a cancellation that was never ours to catch would be a
    real bug in a function meant to be reusable, not just a top-level entry
    point.
    """
    loop = asyncio.get_event_loop()
    task = asyncio.ensure_future(coro_factory())
    registered = []
    shutdown_requested = False

    def _cancel():
        nonlocal shutdown_requested
        shutdown_requested = True
        print("shutting down", file=sys.stderr)
        task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _cancel)
            registered.append(sig)
        except NotImplementedError:
            pass  # no signal handlers on this platform (e.g. Windows)

    try:
        return await task
    except asyncio.CancelledError:
        if not shutdown_requested:
            raise
        return 0
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)


def guard_handler(func, *args, on_error=None, **kwargs):
    """Calls `func(*args, **kwargs)`, isolating one message handler's
    failure from the connection it runs on.

    A 401 (token revoked) always re-raises: `run_forever` needs to see it to
    stop retrying rather than reconnect forever against a dead credential.
    Anything else is caught, reported through `on_error` (stderr by
    default), and swallowed - one member's message must not tear down a
    websocket every other member in the channel is relying on. Returns
    `func`'s result, or `None` if it was caught.
    """
    try:
        return func(*args, **kwargs)
    except Exception as err:
        if is_token_revoked(err):
            raise
        if on_error is not None:
            on_error(err)
        else:
            print(f"handler error: {type(err).__name__}: {err}", file=sys.stderr)
        return None
