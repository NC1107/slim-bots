import asyncio
import os
import signal

import pytest

from slimbots.exceptions import BadArgument
from slimbots.http import ApiError
from slimbots.lifecycle import run_with_shutdown
from slimbots.limits import Cooldown, Quota, RateLimiter, ValidationError, require_int, require_len, require_range
from slimbots.permissions import Permissions
from slimbots.registration import registration_body
from slimbots.testing import FakeAsyncClient


def test_administrator_bypasses_every_bit():
    assert Permissions.contains(Permissions.ADMINISTRATOR, Permissions.MANAGE_ROLES)
    assert Permissions.contains(Permissions.ADMINISTRATOR, Permissions.BAN_MEMBERS)


def test_a_missing_bit_is_denied():
    assert not Permissions.contains(Permissions.SEND_MESSAGES, Permissions.MANAGE_ROLES)


def test_cooldown_resets_after_the_window():
    clock = {"t": 0.0}
    cd = Cooldown(5, clock=lambda: clock["t"])
    assert cd.check("u1") is None
    assert cd.check("u1") is not None
    clock["t"] = 6.0
    assert cd.check("u1") is None


def test_rate_limiter_allows_a_burst_then_blocks():
    clock = {"t": 0.0}
    rl = RateLimiter(2, 10, clock=lambda: clock["t"])
    assert rl.check("u1") is None
    assert rl.check("u1") is None
    assert rl.check("u1") is not None


def test_quota_tracks_use_and_release():
    q = Quota(1)
    assert q.check("u1") is None
    q.use("u1")
    assert q.check("u1") is not None
    q.release("u1")
    assert q.check("u1") is None


def test_require_int_bounds():
    assert require_int("5", min_value=1, max_value=10) == 5
    with pytest.raises(ValidationError):
        require_int("0", min_value=1, max_value=10)
    with pytest.raises(ValidationError):
        require_int("abc")


def test_require_len_bounds():
    assert require_len("hi", max_len=5) == "hi"
    with pytest.raises(ValidationError):
        require_len("way too long", max_len=5)


def test_require_range_bounds():
    assert require_range(5, min_value=1, max_value=10) == 5
    with pytest.raises(ValidationError):
        require_range(11, max_value=10)


async def test_run_with_shutdown_exits_clean_on_sigterm():
    async def forever():
        await asyncio.sleep(100)
        return 1

    task = asyncio.ensure_future(run_with_shutdown(forever))
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    result = await asyncio.wait_for(task, timeout=2)
    assert result == 0


async def test_registration_is_a_noop_on_a_server_without_the_route():
    from slimbots.commands import Command
    from slimbots.registration import register_commands

    async def handler(ctx):
        pass

    client = FakeAsyncClient()
    client.respond("PUT", "/bots/commands", ApiError(404, {"error": "not found"}))
    ok = await register_commands(client, prefix="!", commands=[Command(handler, name="ping")])
    assert ok is False


async def test_registration_is_a_noop_on_405_too():
    from slimbots.commands import Command
    from slimbots.registration import register_commands

    async def handler(ctx):
        pass

    client = FakeAsyncClient()
    client.respond("PUT", "/bots/commands", ApiError(405, {"error": "method not allowed"}))
    ok = await register_commands(client, prefix="!", commands=[Command(handler, name="ping")])
    assert ok is False


async def test_registration_reraises_a_real_error():
    from slimbots.commands import Command
    from slimbots.registration import register_commands

    async def handler(ctx):
        pass

    client = FakeAsyncClient()
    client.respond("PUT", "/bots/commands", ApiError(500, {"error": "boom"}))
    with pytest.raises(ApiError):
        await register_commands(client, prefix="!", commands=[Command(handler, name="ping")])


def test_registration_body_includes_aliases_and_permission():
    from slimbots.commands import Command

    async def handler(ctx):
        pass

    cmd = Command(handler, name="balance", aliases=["bal"], help="show chips", requires=Permissions.MANAGE_ROLES)
    body = registration_body("!", [cmd])
    names = {c["name"] for c in body["commands"]}
    assert names == {"balance", "bal"}
    assert all(c["permission"] == int(Permissions.MANAGE_ROLES) for c in body["commands"])
    assert body["prefix"] == "!"


async def test_catchup_bootstrap_and_sync(tmp_path):
    import sqlite3

    from slimbots import catchup, cursor

    conn = sqlite3.connect(":memory:")
    cursor.init_table(conn)
    client = FakeAsyncClient()
    client.respond("GET", "/channels/c1/messages?limit=1", [{"seq": 42}])
    await catchup.bootstrap(client, conn, "c1")
    assert cursor.get(conn, "c1") == 42

    client.respond("POST", "/sync", {"scopes": [{"channel_id": "c1", "messages": [], "reset": False}]})
    scopes = await catchup.sync(client, [{"channel_id": "c1", "after_seq": 42}])
    assert scopes[0]["channel_id"] == "c1"


async def test_bad_argument_message_names_the_field():
    from slimbots.commands import Command

    async def handler(ctx, amount: int):
        pass

    cmd = Command(handler, name="give")

    class FakeCtx:
        bot = None

    with pytest.raises(BadArgument) as excinfo:
        await cmd.convert_args(FakeCtx(), "notanumber")
    assert "amount" in str(excinfo.value)
