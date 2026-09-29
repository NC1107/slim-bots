"""bot-music's settings, track model, Jellyfin audio search and stream-URL vetting; see README.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" in docs/framework.md."""

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MAX_QUERY_LENGTH = 200
MAX_QUEUE_LENGTH = 50
MAX_SEARCH_RESULTS = 5
USER_AGENT = "slimm-bot-music/1.0"
ROSTER_POLL_SECONDS = 20
AUDIO_FIELDS = "Album,AlbumArtist,Artists,RunTimeTicks"

# Populated once by configure(); empty JELLYFIN_URL means direct URLs only.
JELLYFIN_URL = ""
JELLYFIN_API_KEY = ""


class JellyfinAuthError(Exception):
    """The Jellyfin API key was rejected. Not something to retry."""


@dataclass
class Track:
    title: str
    url: str
    duration_seconds: float = 0.0
    headers: str | None = None


def configure(bot):
    global JELLYFIN_URL, JELLYFIN_API_KEY
    JELLYFIN_URL = (bot.setting("JELLYFIN_URL", "") or "").rstrip("/")
    JELLYFIN_API_KEY = bot.setting("JELLYFIN_API_KEY", "") or ""


def jellyfin_enabled():
    return bool(JELLYFIN_URL and JELLYFIN_API_KEY)


def check_config():
    """A plaintext, non-loopback JELLYFIN_URL leaks the API key; a URL without a key is a half-configured bot."""
    if bool(JELLYFIN_URL) != bool(JELLYFIN_API_KEY):
        return "set both JELLYFIN_URL and JELLYFIN_API_KEY, or neither for direct-URL-only playback"
    if not JELLYFIN_URL:
        return None
    loopback = urllib.parse.urlsplit(JELLYFIN_URL).hostname in ("localhost", "127.0.0.1", "::1")
    if not JELLYFIN_URL.startswith("https://") and not loopback:
        return "refusing a plaintext, non-loopback JELLYFIN_URL: a token on the wire is a leak"
    return None


def jellyfin_auth_header():
    return f'MediaBrowser Token="{JELLYFIN_API_KEY}"'


def jf_get(path, params=None):
    """One authenticated Jellyfin GET returning parsed JSON. Sync (urllib); called via asyncio.to_thread."""
    query = urllib.parse.urlencode(params or {}, doseq=True)
    request = urllib.request.Request(f"{JELLYFIN_URL}{path}?{query}")
    request.add_header("authorization", jellyfin_auth_header())
    request.add_header("user-agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise JellyfinAuthError("jellyfin api key rejected") from err
        raise


def search_tracks(query, limit=MAX_SEARCH_RESULTS):
    params = {"searchTerm": query, "recursive": "true", "includeItemTypes": "Audio", "fields": AUDIO_FIELDS, "limit": limit}
    return jf_get("/Items", params).get("Items", [])


def track_from_item(item):
    """`Static=true` streams the original file untouched; ffmpeg decodes whatever container it is."""
    artist = item.get("AlbumArtist") or ", ".join(item.get("Artists") or [])
    name = item.get("Name") or "Unknown track"
    return Track(
        title=f"{artist} - {name}" if artist else name,
        url=f"{JELLYFIN_URL}/Audio/{item['Id']}/stream?Static=true",
        duration_seconds=(item.get("RunTimeTicks") or 0) / 10_000_000,
        headers=f"Authorization: {jellyfin_auth_header()}\r\n",
    )


def is_stream_url(text):
    text = text.strip()
    parts = urllib.parse.urlsplit(text)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and " " not in text


def _is_private(address):
    ip = ipaddress.ip_address(address)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def check_stream_url(url):
    """Refuses a URL whose host resolves to a private address, so `~play` cannot probe the bot host's LAN.
    The configured Jellyfin host is exempt; a resolver that changes its answer between this check and ffmpeg's fetch is not caught."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if JELLYFIN_URL and host == urllib.parse.urlsplit(JELLYFIN_URL).hostname:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "could not resolve that host."
    if any(_is_private(str(info[4][0])) for info in infos):
        return "that address is on a private network - only public stream URLs are allowed."
    return None


def track_from_url(url):
    parts = urllib.parse.urlsplit(url)
    last_segment = urllib.parse.unquote(parts.path).rstrip("/").rsplit("/", 1)[-1]
    return Track(title=last_segment or parts.hostname or url, url=url)


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
