"""bot-minecraft's settings, log-line parsing, and the text hygiene both directions share; see README.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" in docs/framework.md."""

import json
import re
from collections import namedtuple

EVENT_KINDS = ("chat", "join", "leave", "death", "advancement")
MAX_GAME_CHAT = 256
MAX_SLIM_LINE = 400
MAX_NAME = 16
MAX_RCON_COMMAND = 1400
RELAY_TAG = "[slim]"

Event = namedtuple("Event", "kind player text")

MC_LOG_PATH = ""
MC_CHANNEL = ""
MC_RCON_HOST = ""
MC_RCON_PORT = 25575
MC_RCON_PASSWORD = ""
MC_ANNOUNCE = list(EVENT_KINDS)
MC_BATCH_SECONDS = 3.0
MC_MAX_LINES_PER_POST = 20
MC_MAX_POSTS_PER_MINUTE = 10
MC_QUEUE_MAX = 200
MC_TO_GAME_PER_MINUTE = 30

# Vanilla and Fabric bracket the thread, Paper and Spigot do not, and Forge adds a logger after the level.
_LOG_LINE = re.compile(r"^(?:\[[^\]]*\] \[[^\]/]*/INFO\]|\[[^\]]* INFO\])(?: \[[^\]]*\])?: (.*)$")
_NAME = r"[A-Za-z0-9_.]{1,%d}" % MAX_NAME
_CHAT = re.compile(rf"^(?:\[Not Secure\] )?<({_NAME})> (.+)$")
_JOIN = re.compile(rf"^({_NAME}) joined the game$")
_LEAVE = re.compile(rf"^({_NAME}) left the game$")
_ADVANCEMENT = re.compile(rf"^({_NAME}) has (?:made the advancement|completed the challenge|reached the goal) \[(.+)\]$")
_DEATH = re.compile(
    rf"^({_NAME}) (?:was (?:slain|shot|killed|blown up|fireballed|pummeled|pricked|impaled|squashed|skewered|stung|"
    r"struck by lightning|burnt|roasted|poked|obliterated|doomed|frozen|squished|shot off)|"
    r"fell (?:from|off|out|while)|drowned|burned to death|blew up|hit the ground|starved|suffocated|"
    r"tried to swim|went up in flames|withered away|experienced kinetic energy|froze to death|walked into|"
    r"discovered the floor|didn't want to live|died|left the confines|was killed|"
    r"went off with a bang|walked on danger)\b.*$"
)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SECTION_CODE = re.compile(r"§.?")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\u00ad\u061c\u180e\ufe00-\ufe0f\U000e0000-\U000e007f]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_LIST_PLAYERS = re.compile(r"players online:?\s*(.*)$", re.DOTALL)


def configure(bot):
    """Reads every MC_* variable through the framework's setting() so a bad value is reported at start."""
    global MC_LOG_PATH, MC_CHANNEL, MC_RCON_HOST, MC_RCON_PORT, MC_RCON_PASSWORD, MC_ANNOUNCE
    global MC_BATCH_SECONDS, MC_MAX_LINES_PER_POST, MC_MAX_POSTS_PER_MINUTE, MC_QUEUE_MAX, MC_TO_GAME_PER_MINUTE
    MC_LOG_PATH = bot.setting("MC_LOG_PATH", "", required=True)
    MC_CHANNEL = bot.setting("MC_CHANNEL", "")
    MC_RCON_HOST = bot.setting("MC_RCON_HOST", "")
    MC_RCON_PORT = bot.setting("MC_RCON_PORT", 25575, type=int)
    MC_RCON_PASSWORD = bot.setting("MC_RCON_PASSWORD", "")
    MC_ANNOUNCE = bot.setting("MC_ANNOUNCE", list(EVENT_KINDS), type=list)
    MC_BATCH_SECONDS = max(1.0, bot.setting("MC_BATCH_SECONDS", 3.0, type=float))
    MC_MAX_LINES_PER_POST = max(1, bot.setting("MC_MAX_LINES_PER_POST", 20, type=int))
    MC_MAX_POSTS_PER_MINUTE = max(1, bot.setting("MC_MAX_POSTS_PER_MINUTE", 10, type=int))
    MC_QUEUE_MAX = max(1, bot.setting("MC_QUEUE_MAX", 200, type=int))
    MC_TO_GAME_PER_MINUTE = max(1, bot.setting("MC_TO_GAME_PER_MINUTE", 30, type=int))


def check_config():
    """A one-line reason the settings cannot run, or None."""
    unknown = [kind for kind in MC_ANNOUNCE if kind not in EVENT_KINDS]
    if unknown:
        return f"MC_ANNOUNCE has unknown kinds {unknown}; valid: {', '.join(EVENT_KINDS)}"
    if MC_RCON_HOST and not MC_RCON_PASSWORD:
        return "MC_RCON_HOST is set but MC_RCON_PASSWORD is not"
    if MC_RCON_PASSWORD and not MC_RCON_HOST:
        return "MC_RCON_PASSWORD is set but MC_RCON_HOST is not"
    return None


def rcon_enabled():
    return bool(MC_RCON_HOST and MC_RCON_PASSWORD)


def strip_formatting(text):
    """Drops ANSI, section-sign colour codes, control and invisible characters, and collapses whitespace."""
    text = _SECTION_CODE.sub("", _ANSI.sub("", text))
    text = _CONTROL.sub(" ", _INVISIBLE.sub("", text))
    return " ".join(text.split())


def clip(text, limit):
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def slim_safe(text, limit=MAX_SLIM_LINE):
    """Text fit for a code span in slim-m: no backtick, no live @mention, nothing that can break out or restyle."""
    text = strip_formatting(text).replace("`", "'")
    return clip(text, limit).replace("@", "@​")


def game_safe(text, limit=MAX_GAME_CHAT):
    return clip(strip_formatting(text), limit)


def parse_log_line(line):
    """The Event a server log line carries, or None for anything that is not chat, join, leave, death or advancement."""
    match = _LOG_LINE.match(_ANSI.sub("", line.rstrip("\r\n")))
    if not match:
        return None
    body = match.group(1)
    chat = _CHAT.match(body)
    if chat:
        return Event("chat", chat.group(1), chat.group(2))
    for kind, pattern in (("join", _JOIN), ("leave", _LEAVE)):
        found = pattern.match(body)
        if found:
            return Event(kind, found.group(1), "")
    found = _ADVANCEMENT.match(body)
    if found:
        return Event("advancement", found.group(1), found.group(2))
    found = _DEATH.match(body)
    if found:
        return Event("death", found.group(1), body[len(found.group(1)) + 1:])
    return None


def parse_player_list(response):
    """Names out of a `list` reply, across the 1.7 and the modern wording; None when it is not a player list."""
    match = _LIST_PLAYERS.search(_SECTION_CODE.sub("", response or ""))
    if not match:
        return None
    names = [part.strip() for part in re.split(r"[,\n]", match.group(1))]
    return [name for name in names if re.fullmatch(_NAME, name)]


def format_for_slim(event):
    """One channel line for `event`; player and text are sanitised, the name always sits in its own code span."""
    who = f"`{event.player}`"
    if event.kind == "chat":
        return f"**[mc]** {who}: `{slim_safe(event.text)}`"
    if event.kind == "join":
        return f"**[mc]** {who} joined the game"
    if event.kind == "leave":
        return f"**[mc]** {who} left the game"
    if event.kind == "advancement":
        return f"**[mc]** {who} earned the advancement `{slim_safe(event.text, 120)}`"
    return f"**[mc]** {who} `{slim_safe(event.text, 200)}`"


def is_relay_echo(event):
    """True for chat that carries our own tag, so a line we sent in can never be sent back out."""
    return event.kind == "chat" and event.text.lstrip().startswith(RELAY_TAG)


def game_command(author_name, text):
    """The one-line `tellraw` for a slim-m message; JSON, so no selector or command, and small enough for one RCON packet."""
    name = game_safe(author_name, 32)
    body = game_safe(text)
    while True:
        command = _tellraw(name, body)
        if len(command.encode()) <= MAX_RCON_COMMAND or len(body) <= 3:
            return command
        body = clip(body, len(body) - 8)


def _tellraw(name, body):
    parts = [
        {"text": RELAY_TAG + " ", "color": "aqua"},
        {"text": name + ": ", "color": "yellow"},
        {"text": body, "color": "white"},
    ]
    return "tellraw @a " + json.dumps(parts, ensure_ascii=True)
