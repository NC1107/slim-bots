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
