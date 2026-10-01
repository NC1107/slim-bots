"""Characters that change or hide what a reader sees; the same set as slim-m's `hidden_chars.rs`, so a label is refused here, not by the server's 400."""

from __future__ import annotations

import unicodedata

_HIDDEN_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180E, 0x180E), (0x200B, 0x200F), (0x2028, 0x2029), (0x202A, 0x202E), (0x2060, 0x206F),
    (0x2800, 0x2800), (0x3164, 0x3164), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF9, 0xFFFC),
    (0xE0000, 0xE007F),
)


def is_hidden_char(char: str) -> bool:
    """True for a control, a direction mark or a character that draws nothing; U+FE0F is deliberately legal."""
    code = ord(char)
    return unicodedata.category(char) == "Cc" or any(low <= code <= high for low, high in _HIDDEN_RANGES)


def has_hidden_char(text: str) -> bool:
    return any(is_hidden_char(char) for char in text)
