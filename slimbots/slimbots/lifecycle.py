"""Clean shutdown on SIGTERM/SIGINT (docker sends it on every redeploy), and containing one bad handler's crash."""

import asyncio
import signal
import sys

from .http import is_token_revoked


async def run_with_shutdown(coro_factory):
    """Runs `coro_factory()` to completion, or exits 0 on a clean SIGTERM/SIGINT."""
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(coro_factory())
    shutdown_requested = False

    def cancel():
        nonlocal shutdown_requested
        shutdown_requested = True
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
    except asyncio.CancelledError:
        if shutdown_requested:
            return 0
        raise
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
