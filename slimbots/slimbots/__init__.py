"""A discord.py-shaped bot framework for slim-m; see docs/framework.md."""

from . import catchup, cursor
from .bot import Bot
from .canvas import Canvas
from .client import Client, is_not_found, is_token_revoked, socket_url
from .commands import Command
from .context import Context
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
from .http import AsyncClient as AsyncApiClient
from .limits import Cooldown, Quota, RateLimiter, ValidationError, require_int, require_len, require_range
from .models import Channel, Member, Role
from .permissions import Permissions
from .retry import call_with_retry
from .runner import run_forever
from .space import Space
from .ws import Connection

__all__ = [
    "ApiError",
    "AsyncApiClient",
    "BadArgument",
    "Bot",
    "Canvas",
    "Channel",
    "CheckFailure",
    "Client",
    "Command",
    "CommandError",
    "CommandNotFound",
    "CommandOnCooldown",
    "Connection",
    "Context",
    "Cooldown",
    "Embed",
    "Member",
    "MissingPermissions",
    "MissingRequiredArgument",
    "Permissions",
    "Quota",
    "RateLimiter",
    "Role",
    "SlimBotsError",
    "Space",
    "ValidationError",
    "call_with_retry",
    "catchup",
    "cursor",
    "is_not_found",
    "is_token_revoked",
    "require_int",
    "require_len",
    "require_range",
    "run_forever",
    "socket_url",
]
