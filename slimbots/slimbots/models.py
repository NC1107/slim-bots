"""Typed views of a slim-m member, channel, and role, held by `Space`."""

from .permissions import Permissions


class Member:
    """A slim-m member. `.id` is the stable key to store against - see docs/framework.md."""

    def __init__(self, data, *, base_permissions=0):
        self.id = data["id"]
        self.username = data["username"]
        self.display_name = data["display_name"]
        self.is_bot = bool(data.get("is_bot"))
        self.is_webhook = bool(data.get("is_webhook"))
        self.role_ids = list(data.get("role_ids") or [])
        self.roles = list(data.get("roles") or [])
        self.timed_out_until = data.get("timed_out_until")
        self._base_permissions = base_permissions

    @property
    def storage_key(self):
        """The one value a bot should key its own storage on - stable across renames."""
        return self.id

    def has_permission(self, permission):
        """Whether this member's base (deployment-level) permissions grant `permission`."""
        return Permissions.contains(self._base_permissions, permission)

    def _apply_roles(self, role_ids, base_permissions):
        """Updates the cached role set after a grant/revoke; `Space` owns calling this."""
        self.role_ids = list(role_ids)
        self._base_permissions = base_permissions

    def mention(self):
        return f"@{self.username}"

    def __repr__(self):
        return f"Member(id={self.id!r}, username={self.username!r})"


class Channel:
    """A slim-m channel."""

    def __init__(self, data):
        self.id = data["id"]
        self.name = data["name"]
        self.kind = data.get("kind", "text")
        self.topic = data.get("topic")
        self.category_id = data.get("category_id")

    def __repr__(self):
        return f"Channel(id={self.id!r}, name={self.name!r})"


class Role:
    """A slim-m role, carrying the permission bits `Space` resolves members against."""

    def __init__(self, data):
        self.id = data["id"]
        self.name = data["name"]
        self.permissions = data.get("permissions", 0)
        self.is_everyone = bool(data.get("is_everyone"))

    def __repr__(self):
        return f"Role(id={self.id!r}, name={self.name!r})"


class Attachment:
    """An uploaded attachment's metadata; `.id` is what `send`'s `attachment_ids` takes."""

    def __init__(self, data):
        self.id = data["id"]
        self.content_type = data.get("content_type")
        self.size = data.get("size")
        self.filename = data.get("filename")

    def __repr__(self):
        return f"Attachment(id={self.id!r})"


class DmConversation:
    """One of the caller's direct-message conversations; `.channel_id` works with the ordinary message routes."""

    def __init__(self, data):
        self.channel_id = data["channel_id"]
        self.user = data.get("user") or {}
        self.unread = data.get("unread", 0)
        self.created_at = data.get("created_at")

    def __repr__(self):
        return f"DmConversation(channel_id={self.channel_id!r})"


class Message:
    """A slim-m message, with the actions bound to it: edit, delete, react, pin, vote, open its thread."""

    def __init__(self, data, *, client, channel_id):
        self.id = data["id"]
        self.channel_id = channel_id
        self.seq = data.get("seq")
        self.content = data.get("content")
        self.author_id = data.get("author_id")
        self._client = client
        self._raw = data

    async def edit(self, content):
        """Edits this message; allowed for the author, or a member with MANAGE_MESSAGES."""
        data = await self._client.edit_message(self.channel_id, self.id, content)
        self.content = data.get("content", content)
        return self

    async def delete(self):
        """Soft-deletes this message; deleting an already-deleted one is not an error."""
        await self._client.delete_message(self.channel_id, self.id)

    async def react(self, emoji):
        """Adds `emoji`; idempotent, reacting twice with the same emoji leaves one reaction."""
        await self._client.add_reaction(self.id, emoji)

    async def remove_reaction(self, emoji):
        """Removes the bot's own reaction of `emoji`; idempotent if it was never there."""
        await self._client.remove_reaction(self.id, emoji)

    async def pin(self):
        """Pins this message; idempotent, needs MANAGE_MESSAGES in this channel."""
        await self._client.pin_message(self.channel_id, self.id)

    async def unpin(self):
        """Unpins this message; idempotent, needs MANAGE_MESSAGES in this channel."""
        await self._client.unpin_message(self.channel_id, self.id)

    async def open_thread(self):
        """Opens (or reuses) this message's thread channel; returns a `Channel`."""
        data = await self._client.open_thread(self.channel_id, self.id)
        return Channel(data)

    async def vote(self, option):
        """Casts (or replaces) the bot's own vote on this message's poll, by 0-based option position."""
        await self._client.vote_poll(self.id, option)

    def __repr__(self):
        return f"Message(id={self.id!r}, channel_id={self.channel_id!r})"
