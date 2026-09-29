#!/usr/bin/env python3
"""Structure gate for bot PRs: required files, and a slim-m pin that covers the library features a bot uses."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = ("test_bot.py", "README.md", "requirements.txt")
# bots/ping stays library-free on purpose and has no test_bot.py.
EXEMPT = {"ping": {"test_bot.py"}}

# Feature pattern in bot source -> first slim-m release that provides it (see slimbots/CHANGELOG.md).
FEATURES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    (r"\bunpublish_screen_share\b", "VoiceSession.unpublish_screen_share", (0, 5, 1)),
    (r"\blisten_voice_chats\b|\bmention_commands\b|\bSLIMM_PREFIX\b", "voice-chat listening / @name commands", (0, 5, 0)),
    (r"\b(video_max_bitrate|video_max_framerate|audio_max_bitrate)\b", "publish bitrate ceilings", (0, 4, 3)),
    (r"\.voice\.find_member\b", "bot.voice.find_member", (0, 4, 1)),
    (r"\bensure_columns\b|\bbot\.canvas\(|\bbot\.voice\.join\b", "ensure_columns / bot.canvas / bot.voice.join", (0, 4, 0)),
)

PIN = re.compile(r"^\s*slim-m\s*(?P<op>>=|==|~=|>|<=|<|!=)?\s*(?P<ver>\d[\d.a-z]*)?", re.IGNORECASE)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def dotted(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def pinned_floor(requirements: str) -> tuple[int, ...] | None:
    """The lowest slim-m version the file allows; None when slim-m is not listed at all."""
    for line in requirements.splitlines():
        m = PIN.match(line)
        if not m or line.lstrip().startswith("#"):
            continue
        if m["op"] in (">=", "==", "~=", ">") and m["ver"]:
            return parse_version(m["ver"])
        return (0,)
    return None


def strip_comments_and_strings(source: str) -> str:
    """Drops comments and string literals so a feature named in prose does not count as a use."""
    source = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", source)
    source = re.sub(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', '""', source)
    return re.sub(r"#.*", "", source)


def bot_code(bot_dir: Path) -> str:
    files = sorted(p for p in bot_dir.glob("*.py") if not p.name.startswith("test_"))
    return "\n".join(strip_comments_and_strings(p.read_text()) for p in files)


def check_bot(bot_dir: Path) -> list[str]:
    name = bot_dir.name
    problems = [
        f"bots/{name}/{f} is missing"
        for f in REQUIRED_FILES
        if f not in EXEMPT.get(name, set()) and not (bot_dir / f).is_file()
    ]
    req = bot_dir / "requirements.txt"
    if not req.is_file():
        return problems
    floor = pinned_floor(req.read_text())
    code = bot_code(bot_dir)
    for pattern, label, needed in FEATURES:
        if not re.search(pattern, code):
            continue
        if floor is None:
            problems.append(f"bots/{name}/requirements.txt does not list slim-m but the bot uses {label} (needs >={dotted(needed)})")
        elif floor < needed:
            problems.append(f"bots/{name}/requirements.txt pins slim-m>={dotted(floor)} but {label} needs >={dotted(needed)}")
    return problems


SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def listed_remote_refs(root: Path = ROOT) -> list[str]:
    cmd = ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False).stdout
    return [line for line in out.splitlines() if line and not line.endswith("/HEAD")]


def resolve_base(base: str, root: Path = ROOT) -> str:
    """The commit SHA of the listed origin ref equal to `base`; the argument itself never reaches git."""
    ref = next((r for r in listed_remote_refs(root) if r == base), None)
    if ref is None:
        raise ValueError(f"refusing --base {base!r}: not a remote-tracking ref of origin")
    cmd = ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"]
    sha = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    if SHA.fullmatch(sha) is None:
        raise ValueError(f"refusing --base {base!r}: did not resolve to a commit")
    return sha


def changed_bots(base: str, root: Path = ROOT) -> list[str]:
    sha = resolve_base(base, root)
    cmd = ["git", "diff", "--name-only", f"{sha}...HEAD", "--", "bots"]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True).stdout
    return sorted({p.split("/")[1] for p in out.splitlines() if p.startswith("bots/") and p.count("/") >= 2})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="git ref; check only bots changed since it (default: every bot)")
    args = ap.parse_args(argv)
    try:
        names = changed_bots(args.base) if args.base else sorted(p.name for p in (ROOT / "bots").iterdir() if p.is_dir())
    except ValueError as err:
        ap.error(str(err))
    problems: list[str] = []
    for name in names:
        bot_dir = ROOT / "bots" / name
        if bot_dir.is_dir():
            problems += check_bot(bot_dir)
    for p in problems:
        print(f"FAIL: {p}")
    print(f"checked {len(names)} bot(s): {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
