import asyncio
import os
import uuid

import pytest

from slimbots import Bot, Store


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / f"store-{uuid.uuid4().hex}.db")


def migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS widgets (id TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")


def insert_widget(conn, widget_id):
    conn.execute("INSERT INTO widgets (id, count) VALUES (?, 0)", (widget_id,))


def bump(conn, widget_id):
    conn.execute("UPDATE widgets SET count = count + 1 WHERE id = ?", (widget_id,))
    return conn.execute("SELECT count FROM widgets WHERE id = ?", (widget_id,)).fetchone()[0]


async def test_open_runs_the_migration_hook_once(db_path):
    store = await Store(db_path, migrate=migrate).open()
    await store.run(insert_widget, "w1")
    assert store.connection.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 1
    await store.close()


async def test_run_executes_a_plain_function_against_the_connection(db_path):
    store = await Store(db_path, migrate=migrate).open()
    await store.run(insert_widget, "w1")
    result = await store.run(bump, "w1")
    assert result == 1
    await store.close()


async def test_run_does_not_block_the_event_loop(db_path):
    """A slow query in `run()` still lets other coroutines make progress meanwhile."""
    store = await Store(db_path, migrate=migrate).open()

    def slow_query(conn):
        import time

        time.sleep(0.2)
        return 42

    progressed = []

    async def ticker():
        for _ in range(10):
            await asyncio.sleep(0.02)
            progressed.append(True)

    result, _ = await asyncio.gather(store.run(slow_query), ticker())
    assert result == 42
    assert len(progressed) >= 5
    await store.close()


async def test_calls_are_serialized_even_from_concurrent_callers(db_path):
    store = await Store(db_path, migrate=migrate).open()
    await store.run(insert_widget, "w1")
    await asyncio.gather(*(store.run(bump, "w1") for _ in range(20)))
    final = store.connection.execute("SELECT count FROM widgets WHERE id = 'w1'").fetchone()[0]
    assert final == 20
    await store.close()


async def test_close_is_safe_to_call_when_never_opened(db_path):
    store = Store(db_path, migrate=migrate)
    await store.close()
    assert store.connection is None


async def test_bot_open_store_uses_data_path_and_is_idempotent(db_path):
    bot = Bot(prefix="!", default_data_path=db_path)
    store = await bot.open_store(migrate=migrate)
    assert store is bot.store
    assert store.path == db_path
    again = await bot.open_store(migrate=migrate)
    assert again is store
    await store.close()


async def test_bot_open_store_accepts_an_explicit_path(tmp_path):
    other_path = str(tmp_path / "explicit.db")
    bot = Bot(prefix="!")
    store = await bot.open_store(migrate=migrate, path=other_path)
    assert store.path == other_path
    assert os.path.exists(other_path)
    await store.close()


async def test_store_migrate_opens_the_store_during_start(monkeypatch, db_path):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    bot = Bot(default_data_path=db_path, store_migrate=migrate)
    assert bot.store is None

    async def fake_run_forever():
        return 0

    monkeypatch.setattr(bot, "_run_forever", fake_run_forever)
    await bot.start()
    assert bot.store is not None
    assert bot.store.path == db_path
    assert bot.store.connection is None  # start()'s shutdown already closed it
    assert os.path.exists(db_path)  # but the migration ran and left the file behind
