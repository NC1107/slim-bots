"""One outside HTTP/JSON service: its url and key from the environment, and the blocking call the bots thread out."""

import json
import urllib.error
import urllib.parse
import urllib.request


class AuthError(Exception):
    """The service rejected the bot's api key. Not something to retry."""


class Service:
    """A Sonarr-shaped api: `X-Api-Key` header, JSON in and out, urllib so no second HTTP stack is needed."""

    def __init__(self, name, api_root):
        self.name = name
        self.api_root = api_root
        self.url = ""
        self.key = ""
        self.poll_seconds = 60

    def configure(self, bot):
        """Reads `<NAME>_URL`, `<NAME>_API_KEY` and `<NAME>_POLL_SECONDS` through `bot.setting()`."""
        prefix = self.name.upper()
        self.url = (bot.setting(f"{prefix}_URL", required=True) or "").rstrip("/")
        self.key = bot.setting(f"{prefix}_API_KEY", required=True) or ""
        self.poll_seconds = bot.setting(f"{prefix}_POLL_SECONDS", 60, type=int)

    def problem(self):
        """Plaintext is fine on loopback or to a bare docker service name, which never leaves the host's bridge."""
        if not self.url:
            return None
        host = urllib.parse.urlsplit(self.url).hostname or ""
        private = host in ("localhost", "127.0.0.1", "::1") or "." not in host
        if not self.url.startswith("https://") and not private:
            return f"refusing a plaintext {self.name.upper()}_URL to a public host: the api key on the wire is a leak"
        return None

    def call(self, method, path, params=None, body=None):
        """One authenticated call returning parsed JSON. Sync; called via asyncio.to_thread."""
        query = urllib.parse.urlencode(params or {}, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.url}{self.api_root}{path}?{query}", data=data, method=method)
        request.add_header("x-api-key", self.key)
        request.add_header("user-agent", f"slimm-bot-{self.name}/1.0")
        if data is not None:
            request.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise AuthError(f"{self.name} api key rejected") from err
            raise
