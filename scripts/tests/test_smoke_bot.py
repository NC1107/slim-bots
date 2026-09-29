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
    msgs = [{"id": "r", "author_id": "bot", "content": "hello commands"}, {"id": "q", "author_id": "me", "content": "!help"}]
    step = {"say": "!help", "expect_contains": ["commands", "nope"], "timeout": 3}
    r = s.run_step(step, lambda _t: "q", lambda: msgs, "me", sleep=lambda _d: None)
    assert r["replies"] == ["hello commands"]
    assert r["missing"] == ["nope"]
    assert not r["ok"]


def test_run_step_no_reply_is_failure():
    r = s.run_step({"say": "!x", "timeout": 2}, lambda _t: "q", lambda: [{"id": "q", "author_id": "me"}], "me", sleep=lambda _d: None)
    assert not r["ok"]
    assert "(no reply)" in s.render("b", [r])


def test_run_step_polls_at_most_the_cap():
    polls = []
    s.run_step({"say": "!x", "timeout": 10**9}, lambda _t: "q", lambda: polls.append(1) or [], "me", sleep=lambda _d: None)
    assert len(polls) == s.MAX_TIMEOUT


@pytest.mark.parametrize("bot", ["../etc", "a/b", "A", "-x", "", "a.b", "x\n"])
def test_bad_bot_names_refused(bot):
    with pytest.raises(ValueError):
        s.bot_paths(bot)


def make_repo(root):
    (root / "bots" / "casino").mkdir(parents=True)
    (root / "scripts" / "smoke").mkdir(parents=True)
    (root / "scripts" / "smoke" / "casino.json").write_text("{}")


def test_good_bot_name_resolves_from_listing(tmp_path):
    make_repo(tmp_path)
    name, bot_dir, manifest = s.bot_paths("casino", root=tmp_path)
    assert name == "casino"
    assert bot_dir == (tmp_path / "bots" / "casino").resolve()
    assert manifest == (tmp_path / "scripts" / "smoke" / "casino.json").resolve()


def test_unlisted_bot_and_manifest_refused(tmp_path):
    make_repo(tmp_path)
    with pytest.raises(ValueError):
        s.bot_paths("nope", root=tmp_path)
    with pytest.raises(ValueError):
        s.bot_paths("casino", "../../etc/passwd", root=tmp_path)


def test_symlink_escape_refused(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "outside").mkdir()
    (tmp_path / "bots" / "evil").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        s.bot_paths("evil", root=tmp_path)


def test_manifest_path_is_confined(tmp_path):
    with pytest.raises(ValueError):
        s.confined(tmp_path / ".." / "x.json", tmp_path)


def test_main_refuses_traversal_bot_name(monkeypatch):
    for k, v in GOOD_ENV.items():
        monkeypatch.setenv(k, v)
    assert s.main(["../../etc"]) == 2


@pytest.mark.parametrize("manifest", [
    {"steps": [{"say": "x"}] * (51)},
    {"steps": [{"say": "x", "timeout": 10**9}]},
    {"steps": [{"say": "x", "timeout": "5"}]},
    {"steps": [{"say": "x", "timeout": True}]},
    {"steps": [{"say": "x"}], "ready_seconds": 10**6},
    {"steps": [{"say": "x"}], "env": {"A": 1}},
])
def test_manifest_bounds_enforced(tmp_path, manifest):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        s.load_manifest(p)
