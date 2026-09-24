"""Checking whether the *person who typed a command* holds a permission -
not the bot's own, which is all `GET /me` and `GET /channels/{id}/permissions`
answer (see `crates/slimm-server/src/http/users.rs`'s `MeDto` and
`getChannelPermissions` in `schema/openapi.yaml`, both explicitly the
caller's own effective set). There is no endpoint that answers "does user X
hold permission Y", so a privileged command has to be gated some other way,
and a hardcoded user id list is the wrong other way - it silently goes stale
the moment roles change and gives nobody but the bot's operator a way to see
who it currently trusts.

This reconstructs step 1 of the server's own evaluator instead:
`crates/slimm-server/src/permissions.rs` resolves a user's *base* set as the
`@everyone` role's bits unioned with every role they hold, with
`ADMINISTRATOR` granting everything. `GET /roles` has every role's bitmask;
`GET /users/{id}` has a user's own `role_ids`. Combining them gives exactly
that base set - not the final per-channel-overwrite answer (this deployment
has no endpoint for computing that on someone else's behalf), but the right
level for "should this bot let this person run this command", which is an
operator decision, not a stand-in for the platform's own authorization. The
platform still authorizes every write this bot goes on to make, on its own.

`GET /roles` requires `MANAGE_ROLES`. A bot using this module needs to hold
that role itself - the same escalation trade bot-roles's README already
documents for handing roles out at all.
"""

import time

ADMINISTRATOR = 1 << 0
VIEW_CHANNEL = 1 << 1
SEND_MESSAGES = 1 << 2
MANAGE_MESSAGES = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_ROLES = 1 << 5
KICK_MEMBERS = 1 << 6
BAN_MEMBERS = 1 << 7
CREATE_INVITE = 1 << 8
ADD_REACTIONS = 1 << 9
ATTACH_FILES = 1 << 10
CONNECT = 1 << 11
SPEAK = 1 << 12
USE_CANVAS = 1 << 13
MANAGE_CANVAS = 1 << 14
MANAGE_SERVER = 1 << 15
MENTION_EVERYONE = 1 << 16
RUN_CODE = 1 << 17


class PermissionResolver:
    """Resolves a user's base (deployment-level, pre-channel-overwrite)
    permission bitmask, caching the role catalog for `cache_seconds` so a
    privileged command does not pay a `GET /roles` call every time it runs.
    """

    def __init__(self, client, *, cache_seconds=60):
        self._client = client
        self._cache_seconds = cache_seconds
        self._roles = {}
        self._everyone_bits = 0
        self._roles_at = 0.0

    def _ensure_roles(self):
        if time.monotonic() - self._roles_at <= self._cache_seconds:
            return
        roles = self._client.call("GET", "/roles")
        self._roles = {role["id"]: role["permissions"] for role in roles}
        self._everyone_bits = next(
            (role["permissions"] for role in roles if role.get("is_everyone")), 0
        )
        self._roles_at = time.monotonic()

    def base_permissions(self, user_id):
        """The union of `@everyone` plus every role `user_id` holds."""
        self._ensure_roles()
        profile = self._client.call("GET", f"/users/{user_id}")
        bits = self._everyone_bits
        for role_id in profile.get("role_ids", []):
            bits |= self._roles.get(role_id, 0)
        return bits

    def has_permission(self, user_id, permission_bit):
        """Whether `user_id` holds `permission_bit`, or holds
        `ADMINISTRATOR` (which bypasses every other check, exactly as the
        server's own evaluator does)."""
        bits = self.base_permissions(user_id)
        if bits & ADMINISTRATOR:
            return True
        return (bits & permission_bit) == permission_bit
