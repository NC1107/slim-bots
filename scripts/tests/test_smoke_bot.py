import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smoke_bot as s  # noqa: E402

GOOD_ENV = {"SLIMM_URL": "u", "SLIMM_BOT_TOKEN": "b", "SLIMM_SMOKE_TESTER_TOKEN": "t", "SLIMM_SMOKE_CHANNEL": "c", "SLIMM_SMOKE_PRIVATE": "1"}


def test_refuses_without_secrets_or_private_confirmation():
    assert s.missing_env({}) != []
    assert s.missing_env({**GOOD_ENV, "SLIMM_SMOKE_PRIVATE": "0"}) == ["SLIMM_SMOKE_PRIVATE=1"]
    assert s.missing_env(GOOD_ENV) == []


def test_main_exits_2_and_never_boots_a_bot_without_env(monkeypatch):
    for k in GOOD_ENV:
        monkeypatch.delenv(k, raising=False)
    assert s.main(["greeter"]) == 2


def test_shipped_manifests_parse():
    for path in (Path(__file__).resolve().parent.parent / "smoke").glob("*.json"):
        s.load_manifest(path)


@pytest.mark.parametrize("bad", [{}, {"steps": []}, {"steps": [{"say": ""}]}, {"steps": [{"say": "x", "expect_contains": [1]}]}])
def test_bad_manifest_rejected(tmp_path, bad):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        s.load_manifest(p)


def test_replies_after_takes_only_others_newer_messages():
    msgs = [{"id": "3", "author_id": "bot"}, {"id": "2", "author_id": "me"}, {"id": "1", "author_id": "bot"}]
    assert [m["id"] for m in s.replies_after(msgs, "2", "me")] == ["3"]
    assert s.replies_after(msgs, "gone", "me") == []


def test_run_step_records_reply_and_missing():
    tick = iter(range(100))
    msgs = [{"id": "r", "author_id": "bot", "content": "hello commands"}, {"id": "q", "author_id": "me", "content": "!help"}]
    step = {"say": "!help", "expect_contains": ["commands", "nope"], "timeout": 3}
    r = s.run_step(step, lambda _t: "q", lambda: msgs, "me", sleep=lambda _d: None, clock=lambda: next(tick))
    assert r["replies"] == ["hello commands"] and r["missing"] == ["nope"] and not r["ok"]


def test_run_step_no_reply_is_failure():
    tick = iter(range(100))
    r = s.run_step({"say": "!x", "timeout": 2}, lambda _t: "q", lambda: [{"id": "q", "author_id": "me"}], "me", sleep=lambda _d: None, clock=lambda: next(tick))
    assert not r["ok"] and "(no reply)" in s.render("b", [r])
