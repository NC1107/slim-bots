"""The Approve and Decline buttons on a pending request post, gated on a slim-m permission."""

from __future__ import annotations

import asyncio
import contextlib

from arrkit.guard import UNREACHABLE
from arrkit.service import AuthError
from slimbots import ApiError, Button, Permissions, rows

import seerr_core as core

ID_PREFIX = "seerrreq:"

_texts: dict[str, str] = {}


def remember(message_id, text):
    """Keeps a pending post's text so the decision can be written under it; lost on restart, then a generic line is used."""
    _texts[message_id] = text


def buttons_for(request_id):
    return rows([
        Button("Approve", f"{ID_PREFIX}approve:{request_id}", style="primary"),
        Button("Decline", f"{ID_PREFIX}decline:{request_id}", style="danger"),
    ])


async def _allowed(interaction):
    """None when the presser may decide requests, else the ephemeral sentence explaining why not."""
    bot = interaction.bot
    needed = getattr(Permissions, core.SEERR_APPROVER_PERMISSION)
    if not bot.space.roles:
        return "the bot cannot read roles, so it cannot tell who may approve - an admin needs to give it MANAGE_ROLES."
    try:
        member = await bot.space.resolve_member(interaction.user_id)
    except ApiError:
        return "could not look you up - try again."
    if member.has_permission(needed):
        return None
    return f"only members with {core.SEERR_APPROVER_PERMISSION} can approve or decline requests."


async def on_decision_press(interaction):
    action, _, raw_id = interaction.custom_id[len(ID_PREFIX):].partition(":")
    if action not in ("approve", "decline") or not raw_id.isdigit():
        return
    refusal = await _allowed(interaction)
    if refusal:
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral(refusal)
        return
    await interaction.ack()
    bot = interaction.bot
    verb = "approved" if action == "approve" else "declined"
    try:
        await asyncio.to_thread(core.set_request_state, raw_id, action)
    except (*UNREACHABLE, AuthError):
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral(f"seerr did not accept that - request {raw_id} is unchanged.")
        return
    await bot.store.run(core.mark_announced, [f"r|{raw_id}|{verb}"])
    who = interaction.user_display_name or "a member"
    text = _texts.pop(interaction.message_id, f"Request {raw_id}")
    with contextlib.suppress(ApiError):
        await bot.client.edit_message(interaction.channel_id, interaction.message_id, f"{text}\n{verb.capitalize()} by {who}.")
        await bot.client.edit_components(interaction.channel_id, interaction.message_id, [])
