#!/usr/bin/env python3
"""bot-jellyfin: posts new Jellyfin library items, and answers `!jellyfin search|recent|help`; see README.md.
Split per docs/framework.md: this is the entry point and the poll loop, jellyfin_core.py holds the rest."""

import asyncio
import sys

from slimbots import ApiError, Bot
from slimbots.http import is_token_revoked

import jellyfin_core

bot = Bot(prefix="!", require_channels=True, default_data_path="jellyfin_watch.db", store_migrate=jellyfin_core.init_db)
jellyfin_core.configure(bot)

bot.load_extension("search_cog")


async def upload_poster(poster_item_id):
    """Re-hosts a Jellyfin item's primary image as a slim-m attachment, or None if either step fails."""
    if not poster_item_id:
        return None
    image_bytes = await asyncio.to_thread(jellyfin_core.jf_get_bytes, f"/Items/{poster_item_id}/Images/Primary")
    if not image_bytes:
        return None
    try:
        attachment = await bot.client.upload_attachment(image_bytes, filename="poster.jpg")
        return attachment.id if attachment else None
    except ApiError as err:
        if is_token_revoked(err):
            raise
        return None


async def send_post(entry):
    attachment_id = await upload_poster(entry["poster_item_id"])
    attachment_ids = [attachment_id] if attachment_id else None
    channel_id = jellyfin_core.target_channel(bot, entry.get("library_id"))
    text = jellyfin_core.render_text(entry)
    embed = jellyfin_core.render_embed(entry)
    await bot.client.send(
        channel_id, text, message_id=entry["message_id"], attachment_ids=attachment_ids,
        embeds=[embed.to_wire()], fallback_content=text,
    )


async def poll_once():
    items = await bot.store.run(jellyfin_core.fetch_new_items)
    if not items:
        return
    excluded = [item for item in items if jellyfin_core.is_excluded(item)]
    if excluded:
        await bot.store.run(jellyfin_core.mark_posted, [item["Id"] for item in excluded], quiet=True)
        await bot.store.run(jellyfin_core.advance_cursor, max(item["DateCreated"] for item in excluded))
    postable = [item for item in items if not jellyfin_core.is_excluded(item)]
    for entry in jellyfin_core.build_posts(postable):
        try:
            await send_post(entry)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            break
        await bot.store.run(jellyfin_core.mark_posted, entry["item_ids"])
        await bot.store.run(jellyfin_core.advance_cursor, entry["max_created"])


async def poll_loop():
    """A terminal JellyfinAuthError or revoked slimm token propagates out - bot.background() treats that as fatal."""
    while True:
        try:
            await poll_once()
        except jellyfin_core.JellyfinAuthError:
            print("jellyfin api key rejected - exiting", file=sys.stderr)
            raise
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(jellyfin_core.JELLYFIN_POLL_SECONDS)


_background_started = False


@bot.event
async def on_connect():
    global _background_started
    await bot.store.run(jellyfin_core.bootstrap_cursor)
    if _background_started:
        return
    _background_started = True
    bot.background(poll_loop(), name="jellyfin-poll")


def main():
    problem = jellyfin_core.check_jellyfin_config()
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
