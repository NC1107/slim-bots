"""Turns a release-please body into a short plain message: one line per change, no links, no markup."""

import re

MAX_BULLETS = 8
MAX_MESSAGE = 1499
MAX_LINE = 200

_TRAILING_LINK = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)\s*$")
_BARE_PR = re.compile(r"\s*\(#\d+\)\s*$")
_SCOPE = re.compile(r"^\*\*[^*]+:\*\*\s*")
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _clean(line):
    text = line[2:].strip()
    previous = None
    while previous != text:
        previous = text
        text = _BARE_PR.sub("", _TRAILING_LINK.sub("", text))
    text = _INLINE_LINK.sub(r"\1", _SCOPE.sub("", text)).replace("**", "")
    text = " ".join(text.split())
    return text if len(text) <= MAX_LINE else text[: MAX_LINE - 3].rstrip() + "..."


def bullets(body):
    """Every change in the body, in order, repeats dropped."""
    found = []
    for line in (body or "").splitlines():
        if line.startswith(("* ", "- ")):
            text = _clean(line)
            if text and text not in found:
                found.append(text)
    return found


def _fit(items, limit):
    """The longest prefix of at most MAX_BULLETS items that, with its 'and N more' line, fits in `limit`."""
    shown = items[:MAX_BULLETS]
    while True:
        lines = [f"- {item}" for item in shown]
        if len(shown) < len(items):
            lines.append(f"and {len(items) - len(shown)} more")
        text = "\n".join(lines)
        if len(text) <= limit or len(shown) <= 1:
            return text
        shown = shown[:-1]


def condense(body, limit=MAX_MESSAGE):
    items = bullets(body)
    return _fit(items, limit) if items else ""


def render(product, version, url, body):
    head = f"**{product} {version}**\n{url}"
    text = condense(body, MAX_MESSAGE - len(head) - 2)
    return f"{head}\n\n{text}" if text else head
