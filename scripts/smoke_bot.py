#!/usr/bin/env python3
"""Boots one bot against a real slim-m server and runs its manifest of commands in a private channel.

For the owner or orchestrator, never CI. Needs (all from env, none printed):
  SLIMM_URL                  server root
  SLIMM_BOT_TOKEN            the bot's token
  SLIMM_SMOKE_TESTER_TOKEN   a second account (user or bot) that types the commands
  SLIMM_SMOKE_CHANNEL        a private channel holding only those two accounts
  SLIMM_SMOKE_PRIVATE=1      your statement that the channel really is private
The transcript is written to smoke-<bot>-<timestamp>.md for the PR as evidence.
"""

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
BOT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
MAX_STEPS = 50
MAX_TIMEOUT = 120
MAX_READY = 60
REQUIRED_ENV = ("SLIMM_URL", "SLIMM_BOT_TOKEN", "SLIMM_SMOKE_TESTER_TOKEN", "SLIMM_SMOKE_CHANNEL")


def confined(path: Path, parent: Path) -> Path:
    """The resolved path, refusing anything that lands outside `parent`."""
    resolved = path.resolve()
    if not resolved.is_relative_to(parent.resolve()):
        raise ValueError(f"{path} is outside {parent}")
    return resolved


def bot_paths(bot: str, root: Path = ROOT) -> tuple[Path, Path]:
    """The bot's directory and default manifest, for a name that is a plain directory name."""
    if BOT_NAME.fullmatch(bot) is None:
        raise ValueError(f"bot name {bot!r} must match {BOT_NAME.pattern}")
    return confined(root / "bots" / bot, root / "bots"), confined(root / "scripts" / "smoke" / f"{bot}.json", root / "scripts" / "smoke")


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bot", help="directory name under bots/")
    ap.add_argument("--manifest", type=Path, help="default: scripts/smoke/<bot>.json")
    args = ap.parse_args(argv)
    missing = missing_env(dict(os.environ))
    if missing:
        print(f"refusing to run, set: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        bot_dir, default_manifest = bot_paths(args.bot)
        manifest = load_manifest(confined(args.manifest or default_manifest, ROOT / "scripts" / "smoke"))
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
    out = confined(Path.cwd() / f"smoke-{args.bot}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md", Path.cwd())
    out.write_text(render(args.bot, results))
    failed = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} steps ok, transcript at {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
