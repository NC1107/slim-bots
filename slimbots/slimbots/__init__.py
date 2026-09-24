"""A discord.py-shaped bot framework for slim-m; see docs/framework.md."""

from . import catchup, cursor
from . import events
from .bot import Bot
from .canvas import Canvas
from .client import socket_url
from .commands import Command, Group
from .context import Context
from .converters import Duration, TimeOfDay
from .embeds import Embed
from .exceptions import (
    BadArgument,
    CheckFailure,
    CommandError,
    CommandNotFound,
    CommandOnCooldown,
    MissingPermissions,
    MissingRequiredArgument,
    SlimBotsError,
)
from .http import ApiError
from .http import AsyncClient
from .limits import Cooldown, Quota, RateLimiter, ValidationError, require_int, require_len, require_range
from .models import Attachment, Channel, DmConversation, Member, Message, Role
from .permissions import Permissions
from .space import Space
from .store import Store

__all__ = [
    "ApiError",
    "AsyncClient",
    "Attachment",
    "BadArgument",
    "Bot",
    "Canvas",
    "Channel",
    "CheckFailure",
    "Command",
    "CommandError",
    "CommandNotFound",
    "CommandOnCooldown",
    "Context",
    "Cooldown",
    "DmConversation",
    "Duration",
    "Embed",
    "Group",
    "Member",
    "Message",
    "MissingPermissions",
    "MissingRequiredArgument",
    "Permissions",
    "Quota",
    "RateLimiter",
    "Role",
    "SlimBotsError",
    "Space",
    "Store",
    "TimeOfDay",
    "ValidationError",
    "catchup",
    "cursor",
    "events",
    "require_int",
    "require_len",
    "require_range",
    "socket_url",
]
