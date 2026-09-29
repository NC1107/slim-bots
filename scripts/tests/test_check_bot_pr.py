import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_bot_pr as c  # noqa: E402


def make_bot(root: Path, name: str, *, files=("test_bot.py", "README.md"), req="slim-m>=0.5.2\n", code="print(1)\n") -> Path:
    d = root / "bots" / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("x\n")
    if req is not None:
        (d / "requirements.txt").write_text(req)
    (d / "bot.py").write_text(code)
    return d


def test_complete_bot_passes(tmp_path):
    assert c.check_bot(make_bot(tmp_path, "ok")) == []


@pytest.mark.parametrize("missing", ["test_bot.py", "README.md"])
def test_missing_file_fails(tmp_path, missing):
    files = tuple(f for f in ("test_bot.py", "README.md") if f != missing)
    problems = c.check_bot(make_bot(tmp_path, "b", files=files))
    assert problems == [f"bots/b/{missing} is missing"]


def test_missing_requirements_fails(tmp_path):
    assert c.check_bot(make_bot(tmp_path, "b", req=None)) == ["bots/b/requirements.txt is missing"]


def test_ping_needs_no_test_bot(tmp_path):
    assert c.check_bot(make_bot(tmp_path, "ping", files=("README.md",), req="websockets>=12\n")) == []


def test_pin_below_feature_version_fails(tmp_path):
    bot = make_bot(tmp_path, "b", req="slim-m>=0.3.0\n", code="await session.unpublish_screen_share()\n")
    (problem,) = c.check_bot(bot)
    assert "needs >=0.5.1" in problem
    assert "pins slim-m>=0.3.0" in problem


def test_pin_at_feature_version_passes(tmp_path):
    bot = make_bot(tmp_path, "b", req="slim-m>=0.5.1\n", code="await session.unpublish_screen_share()\n")
    assert c.check_bot(bot) == []


def test_bare_slim_m_counts_as_no_floor(tmp_path):
    bot = make_bot(tmp_path, "b", req="slim-m\n", code="Bot(listen_voice_chats=True)\n")
    assert len(c.check_bot(bot)) == 1


def test_unlisted_slim_m_fails_when_feature_used(tmp_path):
    bot = make_bot(tmp_path, "b", req="httpx>=0.27\n", code="Bot(listen_voice_chats=True)\n")
    assert "does not list slim-m" in c.check_bot(bot)[0]


def test_feature_in_comment_or_string_is_ignored(tmp_path):
    code = '# uses unpublish_screen_share later\nx = "listen_voice_chats"\n"""mention_commands"""\n'
    assert c.check_bot(make_bot(tmp_path, "b", req="slim-m>=0.3.0\n", code=code)) == []


def test_test_files_do_not_count_as_use(tmp_path):
    bot = make_bot(tmp_path, "b", req="slim-m>=0.3.0\n")
    (bot / "test_extra.py").write_text("session.unpublish_screen_share()\n")
    assert c.check_bot(bot) == []


def test_commented_pin_line_is_skipped():
    assert c.pinned_floor("# slim-m>=9.0\nslim-m>=0.5.2\n") == (0, 5, 2)


def test_changed_bots_lists_only_touched_dirs(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    make_bot(tmp_path, "old")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feature")
    make_bot(tmp_path, "new")
    (tmp_path / "README.md").write_text("top\n")
    git("add", ".")
    git("commit", "-qm", "change")
    assert c.changed_bots("main", tmp_path) == ["new"]


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-h", "main..evil", "a b", "", "main;rm", "$(x)", "--"])
def test_hostile_base_refused(ref):
    assert not c.valid_ref(ref)
    with pytest.raises(ValueError):
        c.changed_bots(ref)


@pytest.mark.parametrize("ref", ["origin/main", "main", "feature/x-1.2"])
def test_plain_ref_accepted(ref):
    assert c.valid_ref(ref)


def test_main_rejects_option_like_base():
    with pytest.raises(SystemExit) as exc:
        c.main(["--base=--output=/tmp/x"])
    assert exc.value.code == 2
