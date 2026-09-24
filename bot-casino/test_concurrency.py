#!/usr/bin/env python3
"""Proves the money primitives are safe under real, multi-threaded sqlite concurrency; see README.md."""

import os
import random
import sqlite3
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402


def fresh_db(name):
    path = f"/tmp/slimm-casino-test-{name}-{uuid.uuid4().hex}.db"
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    return path


def cleanup(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def open_conn(path):
    """A fresh connection per thread - real separate connections racing, not one connection shared unsafely."""
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def run_concurrently(fns):
    threads = [threading.Thread(target=fn) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_flip_all_never_overspends():
    """Fifty threads race a `!flip all` for one account; no two may ever see and spend the same chip."""
    path = fresh_db("flip-all")
    setup = open_conn(path)
    bot.init_db(setup)
    user = str(uuid.uuid4())
    setup.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES (?, 1000, 0)", (user,))
    setup.close()

    results = []
    lock = threading.Lock()

    def attempt():
        conn = open_conn(path)
        bot.begin(conn)
        amount = bot.resolve_amount(conn, user, "all")
        ok = bot.try_debit(conn, user, amount) if amount > 0 else False
        conn.execute("COMMIT")
        conn.close()
        with lock:
            results.append((amount, ok))

    run_concurrently([attempt for _ in range(50)])

    check = open_conn(path)
    final_balance = check.execute("SELECT balance FROM accounts WHERE user_id = ?", (user,)).fetchone()[0]
    check.close()
    cleanup(path)

    successes = [amount for amount, ok in results if ok]
    assert final_balance >= 0, f"balance went negative: {final_balance}"
    assert len(successes) == 1, f"expected exactly one !flip all to win the race, got {successes}"
    assert final_balance == 1000 - successes[0], "debited amount does not match the balance drop"
    print("PASS: fifty concurrent `!flip all` attempts, exactly one spent the balance, none overspent")


def test_concurrent_transfers_never_create_or_destroy_money():
    """Two transfers together overdraw the sender; at most one may succeed, and the total stays conserved."""
    path = fresh_db("transfer")
    setup = open_conn(path)
    bot.init_db(setup)
    sender = str(uuid.uuid4())
    recipient_a = str(uuid.uuid4())
    recipient_b = str(uuid.uuid4())
    setup.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES (?, 100, 0)", (sender,))
    setup.close()

    outcomes = []
    lock = threading.Lock()

    def give(recipient, amount):
        conn = open_conn(path)
        bot.begin(conn)
        ok = bot.try_debit(conn, sender, amount)
        if ok:
            bot.credit(conn, recipient, amount)
        conn.execute("COMMIT")
        conn.close()
        with lock:
            outcomes.append(ok)

    run_concurrently([lambda: give(recipient_a, 70), lambda: give(recipient_b, 70)])

    check = open_conn(path)
    total = sum(
        row[0]
        for row in check.execute(
            "SELECT balance FROM accounts WHERE user_id IN (?, ?, ?)", (sender, recipient_a, recipient_b)
        ).fetchall()
    )
    check.close()
    cleanup(path)

    assert outcomes.count(True) == 1, f"expected exactly one transfer to succeed, got {outcomes}"
    assert total == 100, f"total money across sender and both recipients changed: {total}"
    print("PASS: two overlapping transfers that together overdraw the account, exactly one landed")


def test_duplicate_request_id_is_charged_once():
    """Simulates a crash-and-replay: ten threads racing the identical request_id apply exactly once."""
    path = fresh_db("dedupe")
    setup = open_conn(path)
    bot.init_db(setup)
    user = str(uuid.uuid4())
    setup.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES (?, 500, 0)", (user,))
    setup.close()

    request_id = str(uuid.uuid4())
    applied = []
    lock = threading.Lock()

    def process():
        conn = open_conn(path)
        bot.begin(conn)
        if not bot.try_consume_request(conn, request_id):
            conn.execute("ROLLBACK")
            conn.close()
            return
        bot.try_debit(conn, user, 50)
        conn.execute("COMMIT")
        conn.close()
        with lock:
            applied.append(True)

    run_concurrently([process for _ in range(10)])

    check = open_conn(path)
    balance = check.execute("SELECT balance FROM accounts WHERE user_id = ?", (user,)).fetchone()[0]
    check.close()
    cleanup(path)

    assert len(applied) == 1, f"the same request id was applied {len(applied)} times, expected 1"
    assert balance == 450, f"expected exactly one 50-chip debit, balance is {balance}"
    print("PASS: ten concurrent deliveries of one request id, exactly one was applied")


def test_random_mixed_load_conserves_total_money():
    """Random flips and gives across shared accounts; total chips plus losses must equal what was minted."""
    path = fresh_db("mixed")
    setup = open_conn(path)
    bot.init_db(setup)
    users = [str(uuid.uuid4()) for _ in range(5)]
    for u in users:
        setup.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES (?, 1000, 0)", (u,))
    setup.close()
    starting_total = 1000 * len(users)

    minted = []
    destroyed = []
    lock = threading.Lock()

    def worker(seed):
        rng = random.Random(seed)
        conn = open_conn(path)
        for _ in range(40):
            user = rng.choice(users)
            bot.begin(conn)
            if not bot.try_consume_request(conn, str(uuid.uuid4())):
                conn.execute("ROLLBACK")
                continue
            amount = rng.randint(1, 200)
            if not bot.try_debit(conn, user, amount):
                conn.execute("ROLLBACK")
                continue
            if rng.random() < 0.5:
                other = rng.choice(users)
                bot.credit(conn, other, amount)
                conn.execute("COMMIT")
                continue
            won = rng.random() < 0.5
            payout = amount * 2 if won else 0
            if payout:
                bot.credit(conn, user, payout)
            conn.execute("COMMIT")
            with lock:
                if payout:
                    minted.append(payout - amount)
                else:
                    destroyed.append(amount)
        conn.close()

    run_concurrently([lambda seed=i: worker(seed) for i in range(8)])

    check = open_conn(path)
    balances = check.execute(
        f"SELECT balance FROM accounts WHERE user_id IN ({','.join('?' for _ in users)})", users
    ).fetchall()
    check.close()
    cleanup(path)

    for (balance,) in balances:
        assert balance >= 0, f"a balance went negative: {balance}"
    ending_total = sum(b for (b,) in balances)
    expected_total = starting_total + sum(minted) - sum(destroyed)
    assert ending_total == expected_total, (
        f"money was created or destroyed by a race: expected {expected_total}, got {ending_total}"
    )
    print(f"PASS: mixed concurrent load (8 threads x 40 ops), total conserved at {ending_total}")


if __name__ == "__main__":
    test_concurrent_flip_all_never_overspends()
    test_concurrent_transfers_never_create_or_destroy_money()
    test_duplicate_request_id_is_charged_once()
    test_random_mixed_load_conserves_total_money()
    print("all concurrency checks passed")
