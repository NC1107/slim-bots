#!/usr/bin/env python3
"""End to end against FakeAsyncClient, a real log file and a real local RCON socket; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.TemporaryDirectory(prefix="mc-bot-")
LOG = os.path.join(TMP.name, "latest.log")
open(LOG, "w").close()
os.environ.update({"MC_LOG_PATH": LOG, "MC_CHANNEL": "c-mc", "MC_RCON_HOST": "127.0.0.1", "MC_RCON_PASSWORD": "pw"})

import bot as mc  # noqa: E402
import mc_core as core  # noqa: E402
from rcon_fake import FakeRcon  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

PREFIX = "[12:00:01] [Server thread/INFO]: "
MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": [], "roles": []},
    {"id": "b1", "username": "helper", "display_name": "Helper", "is_bot": True, "is_webhook": False, "role_ids": [], "roles": []},
]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def harness(use_rcon=True):
    clock = Clock()
    fake = FakeRcon()
    port = await fake.start()
    core.MC_RCON_PORT = port
    core.MC_RCON_HOST = "127.0.0.1" if use_rcon else ""
    core.MC_RCON_PASSWORD = "pw" if use_rcon else ""
    mc.setup_state(clock=clock)
    mc.bot.commands["online"].cooldown._last.clear()
    client = FakeAsyncClient(me_id="bot-1", base="https://slim.example")
    client.respond("GET", "/members", MEMBERS)
    client.respond("GET", "/channels", [{"id": "c-mc", "name": "minecraft", "kind": "text", "restricted": False}])
    mc.bot.client = client
    mc.bot.space = Space(client)
    mc.bot.authors = AuthorFilter(client, space=mc.bot.space, ignore_bots=True)
    mc.bot.me_id = "bot-1"
    mc.bot.channels = {"c-mc"}
    await mc.bot.space.refresh_members()
    await mc.bot.space.refresh_channels()
    await mc.pump_once()
    return clock, fake, client


def append_log(*lines):
    with open(LOG, "a") as handle:
        handle.write("".join(PREFIX + line + "\n" for line in lines))


async def say(content, author="u1", channel="c-mc", **extra):
    await mc.bot._handle_frame({"type": "message.created", "channel_id": channel, "message": {
        "id": f"m-{content[:8]}", "author_id": author, "channel_id": channel, "content": content, "seq": 1, **extra}})
    await asyncio.sleep(0.02)


def run(scenario, **kwargs):
    async def wrapper():
        clock, fake, client = await harness(**kwargs)
        try:
            await scenario(clock, fake, client)
        finally:
            await fake.stop()
            mc.rcon and mc.rcon.close()

    asyncio.run(wrapper())


def test_game_chat_reaches_the_channel_named_and_escaped():
    async def scenario(clock, fake, client):
        append_log("<Steve> hi @everyone `x`", "Alex joined the game")
        await mc.pump_once()
        assert not client.sent, "batched, not posted at once"
        clock.now += 3
        await mc.pump_once()
        body = client.sent[-1]["content"]
        assert client.sent[-1]["channel_id"] == "c-mc"
        assert "**[mc]** `Steve`: `hi @​everyone 'x'`" in body and "`Alex` joined the game" in body
        assert "@everyone" not in body

    run(scenario)


def test_channel_message_reaches_the_game_as_tellraw_under_the_real_author():
    async def scenario(clock, fake, client):
        await say("hello game")
        command = next(c for c in fake.commands if c.startswith("tellraw"))
        parts = json.loads(command[len("tellraw @a "):])
        assert parts[0]["text"] == "[slim] " and parts[1]["text"] == "Nick: " and parts[2]["text"] == "hello game"

    run(scenario)


def test_a_message_in_another_channel_or_from_a_bot_is_not_relayed():
    async def scenario(clock, fake, client):
        await say("elsewhere", channel="c-other")
        await say("from a bot", author="b1")
        await say("from myself", author="bot-1")
        assert not [c for c in fake.commands if c.startswith("tellraw")]

    run(scenario)


def test_a_command_is_not_relayed_into_the_game():
    async def scenario(clock, fake, client):
        await say("!online")
        assert not [c for c in fake.commands if c.startswith("tellraw")]
        assert "`Steve`" in client.sent[-1]["content"] and "`Alex`" in client.sent[-1]["content"]

    run(scenario)


def test_no_loop_a_relayed_line_seen_in_the_log_is_not_posted_back():
    async def scenario(clock, fake, client):
        await say("ping")
        append_log("<Steve> [slim] Nick: ping")
        clock.now += 3
        await mc.pump_once()
        assert not client.sent

    run(scenario)


def test_a_flood_from_the_game_is_bounded_by_the_post_cap():
    async def scenario(clock, fake, client):
        for second in range(120):
            append_log(*[f"<Steve> spam {second} {i}" for i in range(50)])
            clock.now += 1
            await mc.pump_once()
        assert len(client.sent) <= core.MC_MAX_POSTS_PER_MINUTE * 2 + 1
        assert all(len(m["content"]) <= 1900 for m in client.sent)

    run(scenario)


def test_a_flood_from_the_channel_is_bounded_by_the_to_game_cap():
    async def scenario(clock, fake, client):
        for i in range(core.MC_TO_GAME_PER_MINUTE + 15):
            await say(f"line {i}")
        sent = [c for c in fake.commands if c.startswith("tellraw")]
        assert len(sent) == core.MC_TO_GAME_PER_MINUTE

    run(scenario)


def test_without_rcon_the_bridge_is_one_way_and_online_comes_from_the_log():
    async def scenario(clock, fake, client):
        append_log("Steve joined the game")
        await mc.pump_once()
        await say("anyone there?")
        assert fake.commands == []
        await say("!online")
        reply = client.sent[-1]["content"]
        assert "`Steve`" in reply and "from the log" in reply, (reply, client.sent)

    run(scenario, use_rcon=False)


def test_a_dead_rcon_does_not_break_the_bot():
    async def scenario(clock, fake, client):
        await fake.stop()
        await say("into the void")
        await say("!online")
        assert "from the log" in client.sent[-1]["content"]

    run(scenario)


def test_malformed_log_lines_are_ignored_and_the_pump_survives():
    async def scenario(clock, fake, client):
        append_log("<> x", "\x00\x01", "<Steve>", "<Steve> ok")
        with open(LOG, "ab") as handle:
            handle.write(b"\xff\xfe\n")
        clock.now += 3
        await mc.pump_once()
        assert client.sent[-1]["content"].count("[mc]") == 1

    run(scenario)


def test_a_roster_sync_replaces_the_log_derived_roster():
    async def scenario(clock, fake, client):
        mc.bridge.online = {"Ghost"}
        await mc.sync_roster()
        assert mc.bridge.online == {"Steve", "Alex"}

    run(scenario)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
    TMP.cleanup()
