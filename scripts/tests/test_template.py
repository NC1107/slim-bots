"""bots/_template is what a new bot is copied from, so it has to pass everything a copy will face."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_bot_pr  # noqa: E402
import check_hygiene  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "bots" / "_template"


def copy_as(tmp_path, name="mybot"):
    bot_dir = tmp_path / "bots" / name
    shutil.copytree(TEMPLATE, bot_dir, ignore=shutil.ignore_patterns("__pycache__"))
    return bot_dir


def test_a_copy_has_every_file_the_pr_gate_requires(tmp_path):
    assert check_bot_pr.check_bot(copy_as(tmp_path)) == []


def test_a_copy_passes_the_hygiene_gate(tmp_path):
    copy_as(tmp_path)
    assert check_hygiene.check(tmp_path) == []


def test_a_copy_runs_its_own_tests(tmp_path):
    bot_dir = copy_as(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "slimbots")}
    done = subprocess.run([sys.executable, "test_bot.py"], cwd=bot_dir, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_hygiene_gate_catches_what_it_claims_to(tmp_path):
    (tmp_path / "a.md").write_text("a dash " + chr(0x2014) + " here\n")
    (tmp_path / "b.py").write_text("x = 1\n# one\n# two\n")
    (tmp_path / "c.py").write_text("x = '" + chr(0x2B50) + "'\n")
    (tmp_path / "ok.py").write_text("#!/usr/bin/env python3\n# fine\nx = 1  # trailing\ny = 2  # trailing\n")
    found = "\n".join(check_hygiene.check(tmp_path))
    assert "a.md:1: em dash" in found
    assert "b.py:3: plain comment" in found
    assert "c.py:1: emoji" in found
    assert "ok.py" not in found
