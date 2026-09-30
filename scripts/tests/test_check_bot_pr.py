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


@pytest.mark.parametrize(
    "code",
    [
        "await ctx.reply_ephemeral('hi', embed=Embed(title='x'))\n",
        "await ctx.reply_ephemeral(\n    'hi',\n    attachment_ids=ids,\n)\n",
        "await client.send_ephemeral_to_press(c, i, '', embeds=[e])\n",
    ],
)
def test_private_reply_embeds_and_files_need_0_9_3(tmp_path, code):
    bot = make_bot(tmp_path, "b", req="slim-m>=0.9.2\n", code=code)
    (problem,) = c.check_bot(bot)
    assert "needs >=0.9.3" in problem
    bot = make_bot(tmp_path, "b2", req="slim-m>=0.9.3\n", code=code)
    assert c.check_bot(bot) == []


def test_a_plain_private_reply_keeps_its_old_floor(tmp_path):
    bot = make_bot(tmp_path, "b", req="slim-m>=0.6.0\n", code="await ctx.reply_ephemeral('hi')\n")
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
    git("update-ref", "refs/remotes/origin/main", "main")
    git("checkout", "-qb", "feature")
    make_bot(tmp_path, "new")
    (tmp_path / "README.md").write_text("top\n")
    git("add", ".")
    git("commit", "-qm", "change")
    assert c.changed_bots("origin/main", tmp_path) == ["new"]


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-h", "main..evil", "a b", "", "main;rm", "$(x)", "--", "nonexistent"])
def test_hostile_or_unknown_base_refused(ref):
    with pytest.raises(ValueError):
        c.changed_bots(ref)


def test_option_like_base_is_never_run_as_a_git_option(tmp_path):
    with pytest.raises(ValueError):
        c.resolve_base("--output=" + str(tmp_path / "pwned"), tmp_path)
    assert not (tmp_path / "pwned").exists()


def test_resolve_base_returns_a_sha_for_a_real_ref(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"], cwd=tmp_path, check=True)
    assert c.SHA.fullmatch(c.resolve_base("origin/main", tmp_path))


def test_local_branch_is_not_an_allowed_base(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="remote-tracking"):
        c.resolve_base("main", tmp_path)


def test_main_rejects_option_like_base():
    with pytest.raises(SystemExit) as exc:
        c.main(["--base=--output=/tmp/x"])
    assert exc.value.code == 2


@pytest.mark.parametrize("code", [
    "member = await bot.space.find_member(author_id)\n",
    'ROUTES = bot.setting("ROUTES", {}, type=dict)\n',
    'AUTOPLAY = bot.setting("AUTOPLAY", False, type=bool)\n',
])
def test_the_0_8_features_need_a_0_8_pin(tmp_path, code):
    (problem,) = c.check_bot(make_bot(tmp_path, "b", req="slim-m>=0.7.1\n", code=code))
    assert "needs >=0.8.0" in problem
    assert c.check_bot(make_bot(tmp_path / "ok", "b", req="slim-m>=0.8.0\n", code=code)) == []
