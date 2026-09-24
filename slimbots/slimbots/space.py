"""The whole deployment as one live, refreshable object: `bot.space`; see docs/framework.md."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Channel, Member, Role

if TYPE_CHECKING:
    from .http import AsyncClient


class Space:
    """Getters/setters over a deployment's members, channels and roles."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self.members: dict[str, Member] = {}
        self.channels: dict[str, Channel] = {}
        self.roles: dict[str, Role] = {}

    def _base_permissions(self, role_ids: list[str]) -> int:
        bits = 0
        for role in self.roles.values():
            if role.is_everyone or role.id in role_ids:
                bits |= role.permissions
        return bits

    def _make_member(self, data: dict[str, Any]) -> Member:
        return Member(data, base_permissions=self._base_permissions(data.get("role_ids") or []))

    async def refresh_roles(self) -> dict[str, Role]:
        """Reloads every role; needs the bot's own token to hold MANAGE_ROLES."""
        roles = await self._client.list_roles()
        self.roles = {r["id"]: Role(r) for r in roles}
        return self.roles

    async def refresh_channels(self) -> dict[str, Channel]:
        channels = await self._client.list_channels()
        self.channels = {c["id"]: Channel(c) for c in channels}
        return self.channels

    async def refresh_members(self, *, page_size: int = 200) -> dict[str, Member]:
        """Reloads the full member list, one call per `page_size` members."""
        members: dict[str, Member] = {}
        after = None
        while True:
            page = await self._client.list_members(after=after, limit=page_size)
            if not page:
                break
            for data in page:
                members[data["id"]] = self._make_member(data)
            after = page[-1]["id"]
            if len(page) < page_size:
                break
        self.members = members
        return self.members

    async def fetch_member(self, user_id: str) -> Member:
        """Fetches and caches one member directly, for an author not yet in `.members`."""
        data = await self._client.get_user(user_id)
        member = self._make_member(data)
        self.members[user_id] = member
        return member

    async def get_member(self, id_or_name: str) -> Member | None:
        """A cached member by id or by username/display name (case-insensitive)."""
        member = self.members.get(id_or_name)
        if member:
            return member
        needle = id_or_name.lower()
        for member in self.members.values():
            if member.username.lower() == needle or member.display_name.lower() == needle:
                return member
        return None

    def get_channel(self, id_or_name: str) -> Channel | None:
        channel = self.channels.get(id_or_name)
        if channel:
            return channel
        needle = id_or_name.lower()
        for channel in self.channels.values():
            if channel.name.lower() == needle:
                return channel
        return None

    def get_role(self, id_or_name: str) -> Role | None:
        role = self.roles.get(id_or_name)
        if role:
            return role
        needle = id_or_name.lower()
        for role in self.roles.values():
            if role.name.lower() == needle:
                return role
        return None

    async def grant_role(self, member: Member, role: Role | str) -> Member:
        """Grants `role` (a `Role` or a role id) to `member`, updating the cache in place."""
        role_id = role.id if isinstance(role, Role) else role
        await self._client.assign_role(member.id, role_id)
        if role_id not in member.role_ids:
            new_role_ids = member.role_ids + [role_id]
            member._apply_roles(new_role_ids, self._base_permissions(new_role_ids))
        return member

    async def revoke_role(self, member: Member, role: Role | str) -> Member:
        """Revokes `role` from `member` and updates the cache in place."""
        role_id = role.id if isinstance(role, Role) else role
        await self._client.unassign_role(member.id, role_id)
        remaining = [r for r in member.role_ids if r != role_id]
        member._apply_roles(remaining, self._base_permissions(remaining))
        return member

    async def resolve_member(self, storage_key: str) -> Member:
        """The reverse of `Member.storage_key`: a live `Member` for a stored id."""
        member = await self.get_member(storage_key)
        if member:
            return member
        return await self.fetch_member(storage_key)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """The whole space as one nested dict - for introspection, not the wire."""
        return {
            "members": {mid: vars(m).copy() for mid, m in self.members.items()},
            "channels": {cid: vars(c).copy() for cid, c in self.channels.items()},
            "roles": {rid: vars(r).copy() for rid, r in self.roles.items()},
        }
