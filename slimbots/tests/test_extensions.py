import sys
import types

import pytest

from slimbots import Bot


def _make_module(name, setup):
    module = types.ModuleType(name)
    module.setup = setup
    sys.modules[name] = module
    return module


@pytest.fixture(autouse=True)
def _cleanup_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_load_extension_by_module_object_calls_setup_with_the_bot():
    seen = []
    module = types.ModuleType("an_extension")
    module.setup = lambda bot: seen.append(bot)
    bot = Bot()

    bot.load_extension(module)
    assert seen == [bot]


def test_load_extension_by_name_imports_and_calls_setup():
    seen = []
    _make_module("a_named_extension", lambda bot: seen.append(bot))
    bot = Bot()

    bot.load_extension("a_named_extension")
    assert seen == [bot]


def test_load_extension_registers_commands_declared_inside_setup():
    def setup(bot):
        @bot.command()
        async def ping(ctx):
            await ctx.reply("pong")

    _make_module("a_command_extension", setup)
    bot = Bot()

    bot.load_extension("a_command_extension")
    assert "ping" in bot.commands


def test_loading_the_same_extension_twice_only_calls_setup_once():
    calls = []
    _make_module("an_idempotent_extension", lambda bot: calls.append(1))
    bot = Bot()

    bot.load_extension("an_idempotent_extension")
    bot.load_extension("an_idempotent_extension")
    assert calls == [1]


def test_loading_by_module_object_twice_is_also_idempotent():
    calls = []
    module = types.ModuleType("an_idempotent_module")
    module.setup = lambda bot: calls.append(1)
    bot = Bot()

    bot.load_extension(module)
    bot.load_extension(module)
    assert calls == [1]
