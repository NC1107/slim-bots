"""The Permissions bitmask, mirroring crates/slimm-server/src/permissions.rs."""


class Permissions:
    """Named bits of the deployment-level permission bitmask; values must match the server's exactly."""

    NONE = 0
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

    @classmethod
    def contains(cls, bits, permission):
        """Whether `bits` grants `permission`, with ADMINISTRATOR bypassing all."""
        if bits & cls.ADMINISTRATOR:
            return True
        return (bits & permission) == permission
