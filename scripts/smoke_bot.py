#!/usr/bin/env python3
"""Boots one bot against a real slim-m server and runs its command manifest in a private channel; never CI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = ROOT / "smoke-transcripts"
BOT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
MAX_STEPS = 50
MAX_TIMEOUT = 120
MAX_READY = 60
ENV_HELP = """Needs (all from env, none printed):
  SLIMM_URL                  server root
  SLIMM_BOT_TOKEN            the bot's token
  SLIMM_SMOKE_TESTER_TOKEN   a second account (user or bot) that types the commands
  SLIMM_SMOKE_CHANNEL        a private channel holding only those two accounts
  SLIMM_SMOKE_PRIVATE=1      your statement that the channel really is private
The transcript is written to smoke-transcripts/smoke-<bot>-<timestamp>.md for the PR as evidence."""
REQUIRED_ENV = ("SLIMM_URL", "SLIMM_BOT_TOKEN", "SLIMM_SMOKE_TESTER_TOKEN", "SLIMM_SMOKE_CHANNEL")


def confined(path: Path, parent: Path) -> Path:
    """The resolved path, refusing anything that lands outside `parent`."""
    resolved = path.resolve()
    if not resolved.is_relative_to(parent.resolve()):
        raise ValueError(f"{path} is outside {parent}")
    return resolved


def listed(directory: Path, wanted: str, *, suffix: str = "") -> Path:
    """The entry of `directory` named `wanted`, chosen from a listing so the argument never builds a path itself."""
    entries = sorted(directory.iterdir())
    match = next((e for e in entries if e.name == wanted and e.name.endswith(suffix)), None)
    if match is None:
        raise ValueError(f"{wanted!r} is not one of: {', '.join(e.name for e in entries)}")
    return confined(match, directory)


def bot_paths(bot: str, manifest: str | None = None, root: Path = ROOT) -> tuple[str, Path, Path]:
    """The listed bot name, its directory and its manifest, all derived from directory listings."""
    bot_dir = listed(root / "bots", bot)
    if not bot_dir.is_dir() or BOT_NAME.fullmatch(bot_dir.name) is None:
        raise ValueError(f"{bot!r} is not a bot directory")
    return bot_dir.name, bot_dir, listed(root / "scripts" / "smoke", manifest or f"{bot_dir.name}.json", suffix=".json")


def bounded_number(value: Any, label: str, maximum: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
        raise ValueError(f"{label} must be a number in (0, {maximum}]")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{path.name}: 'steps' must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"{path.name}: at most {MAX_STEPS} steps")
    if not isinstance(data.get("env", {}), dict) or not all(isinstance(v, str) for v in data.get("env", {}).values()):
        raise ValueError(f"{path.name}: 'env' must map names to strings")
    bounded_number(data.get("ready_seconds", 8), f"{path.name}: ready_seconds", MAX_READY)
    for i, step in enumerate(steps):
        bounded_number(step.get("timeout", 15), f"{path.name}: step {i} timeout", MAX_TIMEOUT)
        if not isinstance(step.get("say"), str) or not step["say"]:
            raise ValueError(f"{path.name}: step {i} needs a 'say' string")
        if not all(isinstance(s, str) for s in step.get("expect_contains", [])):
            raise ValueError(f"{path.name}: step {i} 'expect_contains' must be strings")
    return data


def missing_env(env: dict[str, str]) -> list[str]:
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if env.get("SLIMM_SMOKE_PRIVATE") != "1":
        missing.append("SLIMM_SMOKE_PRIVATE=1")
    return missing


def replies_after(messages: list[dict[str, Any]], sent_id: str, tester_id: str) -> list[dict[str, Any]]:
    """Messages newer than ours that someone else wrote; `messages` is newest first."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["id"] == sent_id:
            return list(reversed(out))
        if m.get("author_id") != tester_id:
            out.append(m)
    return []


def run_step(
    step: dict[str, Any], say: Callable[[str], str], fetch: Callable[[], list[dict[str, Any]]],
    tester_id: str, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    sent_id = say(step["say"])
    polls = min(int(step.get("timeout", 15)), MAX_TIMEOUT)
    wanted = step.get("expect_contains", [])
    replies: list[dict[str, Any]] = []
    for _ in range(polls):
        sleep(1)
        replies = replies_after(fetch(), sent_id, tester_id)
        text = "\n".join(r.get("content", "") for r in replies)
        if replies and all(w in text for w in wanted):
            break
    text = "\n".join(r.get("content", "") for r in replies)
    missing = [w for w in wanted if w not in text]
    return {"say": step["say"], "replies": [r.get("content", "") for r in replies], "missing": missing, "ok": bool(replies) and not missing}


def render(bot: str, results: list[dict[str, Any]]) -> str:
    lines = [f"# smoke transcript: {bot}", "", f"run at {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]
    for r in results:
        lines += [f"## {'ok' if r['ok'] else 'FAIL'}: `{r['say']}`", ""]
        lines += [f"> {reply}" for reply in r["replies"]] or ["(no reply)"]
        lines += [f"missing: {', '.join(r['missing'])}"] if r["missing"] else []
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, epilog=ENV_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bot", help="directory name under bots/")
    ap.add_argument("--manifest", help="file name under scripts/smoke/, default <bot>.json")
    args = ap.parse_args(argv)
    missing = missing_env(dict(os.environ))
    if missing:
        print(f"refusing to run, set: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        name, bot_dir, manifest_path = bot_paths(args.bot, args.manifest)
        manifest = load_manifest(manifest_path)
    except ValueError as err:
        print(f"refusing to run: {err}", file=sys.stderr)
        return 2
    base, channel = os.environ["SLIMM_URL"].rstrip("/"), os.environ["SLIMM_SMOKE_CHANNEL"]
    http = httpx.Client(base_url=base, timeout=20, headers={"authorization": f"Bearer {os.environ['SLIMM_SMOKE_TESTER_TOKEN']}"})
    tester_id = http.get("/me").raise_for_status().json()["id"]

    def say(text: str) -> str:
        mid = str(uuid.uuid4())
        http.post(f"/channels/{channel}/messages", json={"id": mid, "content": text}).raise_for_status()
        return mid

    def fetch() -> list[dict[str, Any]]:
        return http.get(f"/channels/{channel}/messages", params={"limit": 50}).raise_for_status().json()

    env = {**os.environ, "SLIMM_CHANNELS": channel, **manifest.get("env", {})}
    proc = subprocess.Popen([sys.executable, "bot.py"], cwd=bot_dir, env=env)
    results: list[dict[str, Any]] = []
    try:
        time.sleep(manifest.get("ready_seconds", 8))
        for step in manifest["steps"]:
            results.append(run_step(step, say, fetch, tester_id))
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    out = TRANSCRIPT_DIR / f"smoke-{name}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md"
    out.write_text(render(name, results))
    failed = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} steps ok, transcript at {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
