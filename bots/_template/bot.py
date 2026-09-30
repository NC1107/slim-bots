#!/usr/bin/env python3
"""Template bot: a typed command, a button, a private reply and durable state; see README.md."""

import sqlite3

from slimbots import Bot, Button, Permissions, rows


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS votes (message_id TEXT, user_id TEXT, choice TEXT, PRIMARY KEY (message_id, user_id))")
    conn.commit()


bot = Bot(prefix="!", default_data_path="template.db", store_migrate=init_db)

MAX_QUESTION = bot.setting("TEMPLATE_MAX_QUESTION", 200, type=int)

CHOICES = ("yes", "no")


def _record(conn, message_id, user_id, choice):
    conn.execute(
        "INSERT INTO votes (message_id, user_id, choice) VALUES (?, ?, ?) "
        "ON CONFLICT(message_id, user_id) DO UPDATE SET choice = excluded.choice",
        (message_id, user_id, choice),
    )
    conn.commit()


def _counts(conn, message_id):
    rows_ = conn.execute("SELECT choice, COUNT(*) FROM votes WHERE message_id = ? GROUP BY choice", (message_id,)).fetchall()
    return {choice: n for choice, n in rows_}


@bot.command(name="vote", help="ask a yes/no question with buttons", usage="<question>", cooldown=5)
async def vote(ctx, question: str):
    if len(question) > MAX_QUESTION:
        await ctx.reply_ephemeral(f"keep the question under {MAX_QUESTION} characters.", public_fallback=True)
        return
    buttons = [Button(choice.title(), f"vote:{choice}", style="primary" if choice == "yes" else "secondary") for choice in CHOICES]
    await bot.client.send(ctx.channel_id, question, reply_to_id=ctx.message.get("id"), components=rows(buttons))


@bot.button(prefix="vote:")
async def on_vote(interaction):
    choice = interaction.custom_id.removeprefix("vote:")
    if choice not in CHOICES:
        await interaction.ack()
        return
    store = await bot.open_store()
    await store.run(_record, interaction.message_id, interaction.user_id, choice)
    await interaction.reply_ephemeral(f"counted: {choice}. press again to change it.")


@bot.command(name="tally", help="show the running count for a question's message", usage="<message id>")
async def tally(ctx, message_id: str):
    if not ctx.author.has_permission(Permissions.MANAGE_MESSAGES):
        await ctx.reply_ephemeral("only someone with Manage Messages can read a tally.", public_fallback=True)
        return
    store = await bot.open_store()
    counts = await store.run(_counts, message_id)
    await ctx.reply(", ".join(f"{choice}: {counts.get(choice, 0)}" for choice in CHOICES))


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
