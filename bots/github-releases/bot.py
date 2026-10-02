#!/usr/bin/env python3
"""bot-github-releases: posts each new slim-m client and server release with condensed notes; see README.md."""

import asyncio
import sys
import time
import uuid
from dataclasses import dataclass

import feed as feedmod
import notes
from slimbots import ApiError, Bot
from slimbots.http import is_token_revoked

MIN_POLL_SECONDS = 60
POST_NAMESPACE = uuid.UUID("5d0c7a8e-3f61-4c1e-9b7a-2e8d4f6a1c33")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen (release_id TEXT PRIMARY KEY, tag TEXT NOT NULL, at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()


def is_initialized(conn):
    return conn.execute("SELECT 1 FROM state WHERE key = 'initialized'").fetchone() is not None


def seen_ids(conn):
    return {row[0] for row in conn.execute("SELECT release_id FROM seen")}


def mark_seen(conn, releases, initialize=False):
    now = int(time.time())
    conn.executemany("INSERT OR IGNORE INTO seen (release_id, tag, at) VALUES (?, ?, ?)", [(str(r["id"]), r["tag_name"], now) for r in releases])
    if initialize:
        conn.execute("INSERT OR IGNORE INTO state (key, value) VALUES ('initialized', '1')")
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="github-releases.db", store_migrate=init_db)


@dataclass
class Config:
    repo: str
    token: str | None
    poll_seconds: int
    prefixes: tuple
    announce_latest: bool


def clamp_poll(seconds):
    return max(seconds, MIN_POLL_SECONDS)


config = Config(
    repo=bot.setting("GH_REPO", "Slim-m-org/slim-m"),
    token=bot.setting("GITHUB_TOKEN", None),
    poll_seconds=clamp_poll(bot.setting("GH_POLL_SECONDS", 600, type=int)),
    prefixes=tuple(bot.setting("GH_TAG_PREFIXES", ["client-v", "server-v"], type=list)),
    announce_latest=bot.setting("GH_ANNOUNCE_LATEST", False, type=bool),
)


def match_prefix(tag):
    return next((p for p in config.prefixes if tag.startswith(p)), None)


def describe(release, prefix):
    """The product name and version a tag spells, like ('client', '0.93.0') for client-v0.93.0."""
    product = prefix.removesuffix("v").rstrip("-_/ ") or "release"
    return product, release["tag_name"][len(prefix):]


def release_url(release):
    url = release.get("html_url") or ""
    if url.startswith("https://github.com/"):
        return url
    return f"https://github.com/{config.repo}/releases/tag/{release['tag_name']}"


def eligible(release):
    return not release.get("draft") and not release.get("prerelease")


def order_key(release):
    return (release.get("published_at") or "", release["id"] if isinstance(release["id"], int) else 0)


async def post(release, prefix):
    assert bot.client is not None and bot.channel is not None, "post runs only once connected to one channel"
    product, version = describe(release, prefix)
    url = release_url(release)
    await bot.client.send(
        bot.channel, notes.render(product, version, url, release.get("body")),
        message_id=str(uuid.uuid5(POST_NAMESPACE, f"{config.repo}|{release['id']}")),
    )


async def first_run(candidates):
    """Records every release as seen so a new deployment stays quiet; GH_ANNOUNCE_LATEST posts the newest one first."""
    if config.announce_latest:
        matching = [r for r in candidates if match_prefix(r["tag_name"])]
        if matching:
            newest = max(matching, key=order_key)
            await post(newest, match_prefix(newest["tag_name"]))
    await bot.store.run(mark_seen, candidates, True)


async def announce_new(candidates):
    already = await bot.store.run(seen_ids)
    for release in sorted((r for r in candidates if str(r["id"]) not in already), key=order_key):
        prefix = match_prefix(release["tag_name"])
        if prefix:
            await post(release, prefix)
        await bot.store.run(mark_seen, [release])


async def poll_once(feed):
    """One cycle; returns seconds GitHub asked us to wait, or None. A failed post leaves the etag unaccepted."""
    result = await asyncio.to_thread(feed.fetch)
    if result.status == "limited":
        print(f"github rate limited ({result.detail}), waiting {result.wait}s", file=sys.stderr)
        return result.wait
    if result.status == "error":
        print(f"github fetch failed: {result.detail}, retrying next cycle", file=sys.stderr)
        return None
    if result.status == "unchanged":
        return None
    candidates = [r for r in result.releases if eligible(r)]
    try:
        if await bot.store.run(is_initialized):
            await announce_new(candidates)
        else:
            await first_run(candidates)
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
        return None
    feed.accept(result)
    return None


def next_delay(wait, poll_seconds):
    return max(wait or 0, poll_seconds)


async def run_forever(feed):
    while True:
        wait = None
        try:
            wait = await poll_once(feed)
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(next_delay(wait, config.poll_seconds))


started = []


@bot.event
async def on_connect():
    if started:
        return
    started.append(True)
    bot.background(run_forever(feedmod.Feed(config.repo, token=config.token)), name="github-releases-poll")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
