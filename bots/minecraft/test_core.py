#!/usr/bin/env python3
"""Parsing, sanitising, log tailing and RCON against a real local socket; run directly: python3 test_core.py."""

import asyncio
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MC_LOG_PATH", "unused.log")

import mc_core as core  # noqa: E402
from logtail import LogTail  # noqa: E402
from rcon import RconAuthError, RconClient, RconError  # noqa: E402
from rcon_fake import FakeRcon  # noqa: E402

MAX_LINE = 8192
VANILLA = "[12:00:01] [Server thread/INFO]: "
FORGE = "[12:00:01] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer/]: "


def test_vanilla_and_forge_chat_both_parse():
    for prefix in (VANILLA, FORGE, "[12:00:01] [Async Chat Thread - #3/INFO]: "):
        assert core.parse_log_line(prefix + "<Steve> hello there") == core.Event("chat", "Steve", "hello there")


def test_secure_chat_warning_prefix_is_dropped():
    assert core.parse_log_line(VANILLA + "[Not Secure] <Steve> hi").player == "Steve"


def test_join_leave_death_and_advancement_parse():
    assert core.parse_log_line(VANILLA + "Steve joined the game").kind == "join"
    assert core.parse_log_line(VANILLA + "Steve left the game").kind == "leave"
    death = core.parse_log_line(VANILLA + "Steve was slain by Zombie")
    assert (death.kind, death.player, death.text) == ("death", "Steve", "was slain by Zombie")
    assert core.parse_log_line(VANILLA + "Steve fell from a high place").kind == "death"
    adv = core.parse_log_line(VANILLA + "Steve has made the advancement [Stone Age]")
    assert (adv.kind, adv.text) == ("advancement", "Stone Age")
    assert core.parse_log_line(VANILLA + "Steve has completed the challenge [Return to Sender]").kind == "advancement"


def test_malformed_and_unrelated_lines_are_none():
    lines = [
        "", "garbage", VANILLA, VANILLA + "<> empty name", VANILLA + "<Steve>", VANILLA + "<bad name> hi",
        VANILLA + "<" + "a" * 17 + "> too long a name", VANILLA + "Done (4.2s)! For help, type \"help\"",
        "[12:00:01] [Server thread/WARN]: <Steve> a warning line, not chat", "[12:00:01] [Server thread/INFO]: [Steve] /say text",
        VANILLA + "* Steve waves", "\x00\x01\x02", "[12:00:01] [Server thread/INFO]: " + "x" * 100000,
    ]
    for line in lines:
        assert core.parse_log_line(line) is None, line


def test_a_chat_message_cannot_fake_a_join_or_a_second_line():
    event = core.parse_log_line(VANILLA + "<Steve> Alex joined the game")
    assert (event.kind, event.player) == ("chat", "Steve")
    assert core.format_for_slim(event) == "**[mc]** `Steve`: `Alex joined the game`"


def test_slim_text_has_no_markup_mention_or_code_breakout():
    out = core.slim_safe("@everyone `hi` **bold** §cred§r \x1b[31mansi\x1b[0m ‮evil\nnewline")
    assert "`" not in out and "\n" not in out and "‮" not in out and "§" not in out and "\x1b" not in out
    assert "@everyone" not in out and "@​everyone" in out


def test_long_text_is_clipped():
    assert len(core.slim_safe("a" * 5000)) <= core.MAX_SLIM_LINE
    assert len(core.game_safe("a" * 5000)) <= core.MAX_GAME_CHAT


def test_game_command_is_one_line_of_json_that_cannot_carry_a_command():
    command = core.game_command('Nick"}]\n/op me', 'hi", "clickEvent": {"action": "run_command"}\n/stop @a §4red')
    assert "\n" not in command and command.startswith("tellraw @a ")
    parts = json.loads(command[len("tellraw @a "):])
    assert [set(p) for p in parts] == [{"text", "color"}] * 3
    assert parts[0]["text"] == "[slim] "
    assert "§" not in parts[2]["text"]


def test_paper_and_spigot_log_format_parses():
    paper = "[12:00:01 INFO]: "
    assert core.parse_log_line(paper + "<Steve> hi") == core.Event("chat", "Steve", "hi")
    assert core.parse_log_line(paper + "[Not Secure] <Steve> hi").kind == "chat"
    assert core.parse_log_line(paper + "Steve joined the game").kind == "join"
    assert core.parse_log_line(paper + "Steve was slain by Zombie").kind == "death"
    assert core.parse_log_line("[12:00:01 WARN]: <Steve> hi") is None


def test_an_overlong_line_is_dropped_whole_not_read_as_its_tail():
    forged = VANILLA + "<Admin> send me your password"
    hostile = "Steve issued server command: /" + "x" * 100 + forged + "y" * (MAX_LINE - len(forged))
    with tempfile.TemporaryDirectory(prefix="mc-tail-") as tmp:
        path = os.path.join(tmp, "latest.log")
        open(path, "w").close()
        tail = LogTail(path)
        tail.read_new()
        with open(path, "a") as handle:
            handle.write(hostile)
        first = tail.read_new()
        with open(path, "a") as handle:
            handle.write("\n" + VANILLA + "<Bob> real\n")
        lines = first + tail.read_new()
        assert [core.parse_log_line(line) for line in lines if core.parse_log_line(line)] == [core.Event("chat", "Bob", "real")]


def test_a_log_that_vanishes_and_returns_is_read_from_its_start():
    with tempfile.TemporaryDirectory(prefix="mc-tail-") as tmp:
        path = os.path.join(tmp, "latest.log")
        with open(path, "w") as handle:
            handle.write("history\n")
        tail = LogTail(path)
        tail.read_new()
        os.remove(path)
        assert tail.read_new() == []
        with open(path, "w") as handle:
            handle.write("first line of the new log\n")
        assert tail.read_new() == ["first line of the new log"]


def test_a_death_line_puts_the_game_supplied_text_in_a_code_span():
    out = core.format_for_slim(core.Event("death", "Steve", "was slain by [click](http://evil.example) **Admin** _x_ <http://e.example>"))
    outside = re.sub(r"`[^`]*`", "", out)
    assert outside == "**[mc]**  "


def test_invisible_and_tag_characters_are_stripped():
    assert core.slim_safe("a\u061cb\u00adc\u180ed\U000e0041e\ufe0ff\u2028g") == "abcdef g"


def test_game_command_fits_one_rcon_packet_for_any_script():
    for text in ("漢" * 256, "\U0001f600" * 256, '"\\' * 128, "é" * 256):
        command = core.game_command("Nick", text)
        assert len(command.encode()) <= core.MAX_RCON_COMMAND, len(command)
        assert json.loads(command[len("tellraw @a "):])[2]["text"]
    assert json.loads(core.game_command("Nick", "a" * 300)[len("tellraw @a "):])[2]["text"] == "a" * 253 + "..."


def test_an_unreadable_list_reply_is_not_an_empty_roster():
    assert core.parse_player_list("Unknown or incomplete command") is None and core.parse_player_list(None) is None


def test_relay_echo_detection():
    assert core.is_relay_echo(core.Event("chat", "Steve", "[slim] Nick: hi"))
    assert not core.is_relay_echo(core.Event("chat", "Steve", "hello [slim]"))


def test_player_list_in_modern_and_old_wording():
    assert core.parse_player_list("There are 2 of a max of 20 players online: Steve, Alex") == ["Steve", "Alex"]
    assert core.parse_player_list("There are 0 of a max of 20 players online: ") == []
    assert core.parse_player_list("There are 2/20 players online:\nSteve, Alex") == ["Steve", "Alex"]
    assert core.parse_player_list("§6There are 1 of a max of 20 players online: §fSteve") == ["Steve"]
    assert core.parse_player_list("nonsense") is None


def test_logtail_starts_at_the_end_and_follows_appends_rotation_and_partial_lines():
    with tempfile.TemporaryDirectory(prefix="mc-tail-") as tmp:
        path = os.path.join(tmp, "latest.log")
        tail = LogTail(path)
        assert tail.read_new() == [], "a missing file is not an error"
        with open(path, "w") as handle:
            handle.write("old history\n")
        assert tail.read_new() == [], "first sight of a file starts at its end"
        with open(path, "a") as handle:
            handle.write("one\ntw")
        assert tail.read_new() == ["one"]
        with open(path, "a") as handle:
            handle.write("o\n")
        assert tail.read_new() == ["two"]
        os.replace(path, path + ".1")
        with open(path, "w") as handle:
            handle.write("fresh\n")
        assert tail.read_new() == ["fresh"], "a replaced file is read from its start"
        with open(path, "w") as handle:
            handle.write("x\n")
        assert tail.read_new() == ["x"], "a truncated file is read from its start"
        with open(path, "ab") as handle:
            handle.write(b"\xff\xfe bad bytes\n" + b"y" * 20000 + b"\nok\n")
        assert tail.read_new()[-1] == "ok"


def test_rcon_round_trip_and_reconnect_after_the_server_drops():
    async def scenario():
        fake = FakeRcon()
        port = await fake.start()
        client = RconClient("127.0.0.1", port, "pw")
        assert "Steve" in await client.command("list")
        await client.command("tellraw @a {}")
        assert fake.commands == ["list", "tellraw @a {}"] and fake.logins == 1
        client.close()
        await client.command("list")
        assert fake.logins == 2
        await fake.stop()
        try:
            await client.command("list")
        except RconError as err:
            assert "pw" not in str(err)
        else:
            raise AssertionError("an unreachable server must raise RconError")

    asyncio.run(scenario())


def test_rcon_wrong_password_raises_auth_error_without_leaking_it():
    async def scenario():
        fake = FakeRcon(password="right")
        port = await fake.start()
        client = RconClient("127.0.0.1", port, "wrong-secret")
        try:
            await client.command("list")
        except RconAuthError as err:
            assert "wrong-secret" not in str(err) and "right" not in str(err)
        else:
            raise AssertionError("a bad password must raise")
        await fake.stop()

    asyncio.run(scenario())


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
