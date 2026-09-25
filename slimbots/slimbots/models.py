"""Typed views of a slim-m member, channel, and role, held by `Space`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .permissions import Permissions

if TYPE_CHECKING:
    from .http import AsyncClient


class Member:
    """A slim-m member. `.id` is the stable key to store against - see docs/framework.md."""

    def __init__(self, data: dict[str, Any], *, base_permissions: int = 0) -> None:
        self.id: str = data["id"]
        self.username: str = data["username"]
        self.display_name: str = data["display_name"]
        self.is_bot = bool(data.get("is_bot"))
        self.is_webhook = bool(data.get("is_webhook"))
        self.role_ids: list[str] = list(data.get("role_ids") or [])
        self.roles: list[str] = list(data.get("roles") or [])
        self.timed_out_until = data.get("timed_out_until")
        self._base_permissions = base_permissions

    @property
    def storage_key(self) -> str:
        """The one value a bot should key its own storage on - stable across renames."""
        return self.id

    def has_permission(self, permission: int) -> bool:
        """Whether this member's base (deployment-level) permissions grant `permission`."""
        return Permissions.contains(self._base_permissions, permission)

    def _apply_roles(self, role_ids: list[str], base_permissions: int) -> None:
        """Updates the cached role set after a grant/revoke; `Space` owns calling this."""
        self.role_ids = list(role_ids)
        self._base_permissions = base_permissions

    def mention(self) -> str:
        return f"@{self.username}"

    def __repr__(self) -> str:
        return f"Member(id={self.id!r}, username={self.username!r})"


class Channel:
    """A slim-m channel."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.kind = data.get("kind", "text")
        self.topic = data.get("topic")
        self.category_id = data.get("category_id")
        # True when @everyone lacks VIEW_CHANNEL here - present only on listChannels rows.
        self.restricted = bool(data.get("restricted", False))

    def __repr__(self) -> str:
        return f"Channel(id={self.id!r}, name={self.name!r})"


class Role:
    """A slim-m role, carrying the permission bits `Space` resolves members against."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.permissions: int = data.get("permissions", 0)
        self.is_everyone = bool(data.get("is_everyone"))

    def __repr__(self) -> str:
        return f"Role(id={self.id!r}, name={self.name!r})"


class Attachment:
    """An uploaded attachment's metadata; `.id` is what `send`'s `attachment_ids` takes."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.content_type = data.get("content_type")
        self.size = data.get("size")
        self.filename = data.get("filename")

    def __repr__(self) -> str:
        return f"Attachment(id={self.id!r})"


class DmConversation:
    """One of the caller's direct-message conversations; `.channel_id` works with the ordinary message routes."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.channel_id: str = data["channel_id"]
        self.user = data.get("user") or {}
        self.unread = data.get("unread", 0)
        self.created_at = data.get("created_at")

    def __repr__(self) -> str:
        return f"DmConversation(channel_id={self.channel_id!r})"


class Message:
    """A slim-m message, with the actions bound to it: edit, delete, react, pin, vote, open its thread."""

    def __init__(self, data: dict[str, Any], *, client: AsyncClient, channel_id: str) -> None:
        self.id: str = data["id"]
        self.channel_id = channel_id
        self.seq = data.get("seq")
        self.content = data.get("content")
        self.author_id = data.get("author_id")
        self.author_display_name = data.get("author_display_name")
        self.attachments: list[dict[str, Any]] = list(data.get("attachments") or [])
        self._client = client
        self._raw = data

    @classmethod
    async def fetch(cls, client: AsyncClient, channel_id: str, message_id: str) -> Message:
        """One message by id, even one this bot never saw live - see `AsyncClient.get_message`."""
        return await client.get_message(channel_id, message_id)

    async def edit(self, content: str) -> Message:
        """Edits this message; allowed for the author, or a member with MANAGE_MESSAGES."""
        data = await self._client.edit_message(self.channel_id, self.id, content)
        self.content = data.get("content", content)
        return self

    async def delete(self) -> None:
        """Soft-deletes this message; deleting an already-deleted one is not an error."""
        await self._client.delete_message(self.channel_id, self.id)

    async def react(self, emoji: str) -> None:
        """Adds `emoji`; idempotent, reacting twice with the same emoji leaves one reaction."""
        await self._client.add_reaction(self.id, emoji)

    async def remove_reaction(self, emoji: str) -> None:
        """Removes the bot's own reaction of `emoji`; idempotent if it was never there."""
        await self._client.remove_reaction(self.id, emoji)

    async def pin(self) -> None:
        """Pins this message; idempotent, needs MANAGE_MESSAGES in this channel."""
        await self._client.pin_message(self.channel_id, self.id)

    async def unpin(self) -> None:
        """Unpins this message; idempotent, needs MANAGE_MESSAGES in this channel."""
        await self._client.unpin_message(self.channel_id, self.id)

    async def open_thread(self) -> Channel:
        """Opens (or reuses) this message's thread channel; returns a `Channel`."""
        data = await self._client.open_thread(self.channel_id, self.id)
        return Channel(data)

    async def vote(self, option: int) -> None:
        """Casts (or replaces) the bot's own vote on this message's poll, by 0-based option position."""
        await self._client.vote_poll(self.id, option)

    def __repr__(self) -> str:
        return f"Message(id={self.id!r}, channel_id={self.channel_id!r})"
