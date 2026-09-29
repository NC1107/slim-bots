"""Buttons for a bot's message (slim-m decision 0038): `Button` and `rows()`, the wire shape `send(components=...)` takes."""

from __future__ import annotations

from typing import Any, Sequence, Union

STYLES = ("primary", "secondary", "danger", "link")
MAX_ROWS = 5
MAX_BUTTONS_PER_ROW = 5
MAX_LABEL = 80
MAX_CUSTOM_ID = 100


class Button:
    """One button; a link button takes a `url` and never reaches the bot, any other takes a `custom_id`."""

    def __init__(
        self, label: str, custom_id: str | None = None, *, style: str = "secondary", url: str | None = None,
        disabled: bool = False,
    ) -> None:
        if style not in STYLES:
            raise ValueError(f"button style must be one of {STYLES}, not {style!r}")
        if len(label) > MAX_LABEL:
            raise ValueError(f"button label is over {MAX_LABEL} characters")
        if style == "link":
            if not url or custom_id:
                raise ValueError("a link button takes a url and no custom_id")
        elif not custom_id or url or len(custom_id) > MAX_CUSTOM_ID:
            raise ValueError(f"a button takes a custom_id of 1 to {MAX_CUSTOM_ID} characters and no url")
        self.label = label
        self.custom_id = custom_id
        self.style = style
        self.url = url
        self.disabled = disabled

    @classmethod
    def link(cls, label: str, url: str) -> Button:
        return cls(label, style="link", url=url)

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"label": self.label, "style": self.style}
        if self.custom_id:
            wire["custom_id"] = self.custom_id
        if self.url:
            wire["url"] = self.url
        if self.disabled:
            wire["disabled"] = True
        return wire


Rows = Sequence[Union[Sequence[Button], "dict[str, Any]"]]


def rows(*layout: Sequence[Button] | dict[str, Any]) -> list[dict[str, Any]]:
    """`rows([Button("Hit", "hit"), Button("Stand", "stand")], [...])` as the `components` a send takes."""
    return to_wire(layout)


def to_wire(layout: Rows) -> list[dict[str, Any]]:
    """Accepts rows of `Button`s or ready-made wire dicts; the server enforces the same caps on either."""
    if len(layout) > MAX_ROWS:
        raise ValueError(f"a message holds at most {MAX_ROWS} rows of buttons")
    wire: list[dict[str, Any]] = []
    for row in layout:
        if isinstance(row, dict):
            wire.append(row)
            continue
        if len(row) > MAX_BUTTONS_PER_ROW:
            raise ValueError(f"a row holds at most {MAX_BUTTONS_PER_ROW} buttons")
        wire.append({"buttons": [b.to_wire() for b in row]})
    return wire
