"""Clean shutdown on SIGTERM/SIGINT (docker sends it on every redeploy), and containing one bad handler's crash."""

import asyncio
import signal
import sys

from .http import is_token_revoked


async def run_with_shutdown(task):
    """Awaits `task`; SIGTERM/SIGINT cancel it. The CancelledError always propagates - the caller decides what it means."""
    loop = asyncio.get_running_loop()

    def cancel():
        print("shutting down", file=sys.stderr)
        task.cancel()

    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, cancel)
            installed.append(sig)
        except NotImplementedError:
            pass

    try:
        return await task
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


async def guard_dispatch(func, *args, on_error=None, **kwargs):
    """Runs one dispatched handler, swallowing anything but a revoked token (401 must stop the loop)."""
    try:
        return await func(*args, **kwargs)
    except Exception as err:
        if is_token_revoked(err):
            raise
        if on_error:
            await on_error(err)
        else:
            print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return None
