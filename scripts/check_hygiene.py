#!/usr/bin/env python3
"""Repo rules CI enforces on contributed text: no em dash, no emoji, plain comments one line."""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "smoke-transcripts", "LICENSES"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml"}
EM_DASH = chr(0x2014)
GLYPH_RANGES = ((0x2190, 0x21FF), (0x2300, 0x23FF), (0x25A0, 0x27BF), (0x2B00, 0x2BFF), (0xFE0F, 0xFE0F), (0x1F000, 0x1FAFF))
LOCKFILE = re.compile(r".*\.lock\.txt")


def tracked_text_files(root: Path = ROOT) -> list[Path]:
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part in EXCLUDE_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.suffix in TEXT_SUFFIXES and not LOCKFILE.fullmatch(path.name):
            found.append(path)
    return found


def character_problems(path: Path, text: str) -> list[str]:
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        if EM_DASH in line:
            problems.append(f"{path}:{number}: em dash, use a plain hyphen")
        if any(lo <= ord(ch) <= hi for ch in line for lo, hi in GLYPH_RANGES):
            problems.append(f"{path}:{number}: emoji or decorative glyph")
    return problems


def comment_run_problems(path: Path, text: str) -> list[str]:
    """Flags two or more consecutive own-line `#` comments; shebangs, pragmas and trailing comments do not count."""
    lines = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            own_line = tok.type == tokenize.COMMENT and not tok.line[: tok.start[1]].strip()
            if own_line and not tok.start[0] == 1 and not re.match(r"#\s*(noqa|type:|pyright:|pragma)", tok.string):
                lines.append(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        return []
    return [f"{path}:{b}: plain comment runs past one line" for a, b in zip(lines, lines[1:]) if b == a + 1]


def check(root: Path = ROOT) -> list[str]:
    problems = []
    for path in tracked_text_files(root):
        text = path.read_text(encoding="utf-8")
        problems += character_problems(path.relative_to(root), text)
        if path.suffix == ".py":
            problems += comment_run_problems(path.relative_to(root), text)
    return problems


def main() -> int:
    problems = check()
    for p in problems:
        print(f"FAIL: {p}")
    print("hygiene: ok" if not problems else f"hygiene: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
