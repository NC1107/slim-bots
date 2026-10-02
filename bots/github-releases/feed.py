"""Fetches GitHub's release list with an ETag, and turns rate limits and bad answers into results instead of exceptions."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

MAX_WAIT = 3600


@dataclass
class Result:
    status: str
    releases: list = field(default_factory=list)
    etag: str | None = None
    wait: int | None = None
    detail: str = ""


def urllib_transport(url, headers):
    """One GET returning (status, lowercase headers, body); a non-2xx answer is a result, not an exception."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as err:
        return err.code, {k.lower(): v for k, v in err.headers.items()}, err.read()


class Feed:
    """The etag moves only on `accept`, so a list whose posts failed is fetched in full again next cycle."""

    def __init__(self, repo, token=None, transport=urllib_transport, clock=time.time):
        self.url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
        self.token = token
        self.transport = transport
        self.clock = clock
        self.etag = None

    def _headers(self):
        headers = {"accept": "application/vnd.github+json", "user-agent": "slimm-bot-github-releases/1.0", "x-github-api-version": "2022-11-28"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if self.etag:
            headers["if-none-match"] = self.etag
        return headers

    def fetch(self):
        try:
            status, headers, body = self.transport(self.url, self._headers())
        except Exception as err:
            return Result("error", detail=f"{type(err).__name__}")
        if status == 304:
            return Result("unchanged")
        if status in (403, 429):
            wait = self._limit_wait(status, headers)
            if wait is not None:
                return Result("limited", wait=wait, detail=f"{status}")
        if status != 200:
            return Result("error", detail=f"http {status}")
        return self._parse(body, headers.get("etag"))

    def _limit_wait(self, status, headers):
        if "retry-after" in headers:
            return self._capped(headers["retry-after"], lambda v: int(float(v)))
        if status == 429 or headers.get("x-ratelimit-remaining") == "0":
            return self._capped(headers.get("x-ratelimit-reset", ""), lambda v: int(float(v) - self.clock()))
        return None

    @staticmethod
    def _capped(raw, convert):
        try:
            return max(0, min(convert(raw), MAX_WAIT))
        except ValueError:
            return MAX_WAIT

    @staticmethod
    def _parse(body, etag):
        try:
            data = json.loads(body)
        except ValueError:
            return Result("error", detail="invalid json")
        if not isinstance(data, list):
            return Result("error", detail="not a list")
        releases = [r for r in data if isinstance(r, dict) and "id" in r and isinstance(r.get("tag_name"), str)]
        return Result("ok", releases=releases, etag=etag)

    def accept(self, result):
        if result.status == "ok":
            self.etag = result.etag
