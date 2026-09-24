import sqlite3

from slimbots.migrations import ensure_columns


def a_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE widgets (id TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO widgets (id, count) VALUES ('w1', 5)")
    conn.commit()
    return conn


def test_adds_a_missing_column():
    conn = a_table()
    ensure_columns(conn, "widgets", {"label": "TEXT"})
    columns = {row[1] for row in conn.execute("PRAGMA table_info(widgets)")}
    assert "label" in columns


def test_an_existing_row_survives_with_the_new_column_at_its_default():
    conn = a_table()
    ensure_columns(conn, "widgets", {"active": "INTEGER NOT NULL DEFAULT 1"})
    row = conn.execute("SELECT id, count, active FROM widgets WHERE id = 'w1'").fetchone()
    assert row == ("w1", 5, 1)


def test_a_column_that_already_exists_is_left_alone():
    conn = a_table()
    ensure_columns(conn, "widgets", {"count": "INTEGER"})
    row = conn.execute("SELECT count FROM widgets WHERE id = 'w1'").fetchone()
    assert row == (5,)


def test_adds_several_columns_in_one_call():
    conn = a_table()
    ensure_columns(conn, "widgets", {"label": "TEXT", "weight": "REAL"})
    columns = {row[1] for row in conn.execute("PRAGMA table_info(widgets)")}
    assert {"label", "weight"} <= columns


def test_calling_it_twice_is_a_no_op_the_second_time():
    conn = a_table()
    ensure_columns(conn, "widgets", {"label": "TEXT"})
    conn.execute("UPDATE widgets SET label = 'x' WHERE id = 'w1'")
    conn.commit()
    ensure_columns(conn, "widgets", {"label": "TEXT"})
    row = conn.execute("SELECT label FROM widgets WHERE id = 'w1'").fetchone()
    assert row == ("x",)
