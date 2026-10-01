"""Message menu entries and call controls a bot registers (slim-m decision 0045), and how `@bot.message_menu` and `@bot.call_control` find their handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .hidden_chars import has_hidden_char
from .http import ApiError
from .registration import RegistrationRejected

if TYPE_CHECKING:
    from .http import AsyncClient
    from .interactions import Interaction

MAX_MENU_ENTRIES = 5
MAX_CALL_CONTROLS = 8
MAX_LABEL = 32
MAX_ID = 64
ICONS = ("play", "pause", "stop", "skip_next", "skip_previous", "volume", "volume_off", "repeat", "shuffle", "list")
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

UiHandler = Callable[["Interaction"], Awaitable[Any]]


@dataclass(frozen=True)
class UiEntry:
    """One registered entry; `icon` is for call controls only, `permission` is one `Permissions` bit."""

    id: str
    label: str
    icon: str | None = None
    permission: int | None = None

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > MAX_ID or not set(self.id) <= _ID_CHARS:
            raise ValueError(f"an entry id is 1 to {MAX_ID} letters, digits, - _ or .")
        if not self.label.strip() or len(self.label.strip()) > MAX_LABEL:
            raise ValueError(f"an entry label is 1 to {MAX_LABEL} characters")
        if has_hidden_char(self.label):
            raise ValueError("an entry label cannot hold control or invisible characters")
        if self.icon is not None and self.icon not in ICONS:
            raise ValueError(f"icon must be one of {ICONS}, not {self.icon!r}")

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"id": self.id, "label": self.label.strip()}
        if self.icon is not None:
            wire["icon"] = self.icon
        if self.permission is not None:
            wire["permission"] = int(self.permission)
        return wire


class UiRoutes:
    """The handlers registered for each surface, by entry id."""

    def __init__(self) -> None:
        self.menu: dict[str, tuple[UiEntry, UiHandler]] = {}
        self.controls: dict[str, tuple[UiEntry, UiHandler]] = {}

    def add(self, surface: str, entry: UiEntry, func: UiHandler) -> None:
        table, cap = (self.menu, MAX_MENU_ENTRIES) if surface == "message_menu" else (self.controls, MAX_CALL_CONTROLS)
        if entry.id in table:
            raise ValueError(f"{surface} entry `{entry.id}` is already registered")
        if len(table) >= cap:
            raise ValueError(f"a bot registers at most {cap} {surface} entries")
        if surface == "message_menu" and entry.icon is not None:
            raise ValueError("only a call control takes an icon")
        table[entry.id] = (entry, func)

    def handler(self, kind: str, entry_id: str) -> UiHandler | None:
        table = self.menu if kind == "message_menu" else self.controls
        found = table.get(entry_id)
        return found[1] if found else None

    def body(self) -> dict[str, Any]:
        return {
            "message_menu": [e.to_wire() for e, _ in self.menu.values()],
            "call_controls": [e.to_wire() for e, _ in self.controls.values()],
        }

    def __bool__(self) -> bool:
        return bool(self.menu or self.controls)


async def register_ui(client: AsyncClient, routes: UiRoutes) -> bool:
    """PUT /bots/ui with the bot's whole set; False, quietly, against a server too old to have the route."""
    try:
        await client.call("PUT", "/bots/ui", routes.body())
        return True
    except ApiError as err:
        if err.status in (404, 405):
            return False
        if err.status == 400:
            raise RegistrationRejected("/bots/ui", err) from err
        raise
