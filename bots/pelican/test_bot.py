#!/usr/bin/env python3
"""Command-layer tests against FakeAsyncClient and a real local fake Pelican API; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.update({"PELICAN_URL": "https://panel.invalid", "PELICAN_API_KEY": "ptlc_secret_key_123"})
os.environ["PELICAN_CONTROL_ROLE"] = "r-ops"
os.environ["PELICAN_LOG_CHANNEL"] = "log1"
os.environ["PELICAN_STATUS_CHANNEL"] = "status1"

import bot as pelican  # noqa: E402
import pelican_core as core  # noqa: E402
from slimbots import ApiError  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.context import Context  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

KEY = "ptlc_secret_key_123"


def server_attrs(identifier, name, memory=2048, suspended=False):
    return {"object": "server", "attributes": {
        "identifier": identifier, "name": name, "node": "node-1", "is_suspended": suspended, "limits": {"memory": memory},
    }}


SERVERS = [server_attrs("aaaa1111", "Minecraft"), server_attrs("bbbb2222", "Minecraft Modded"), server_attrs("cccc3333", "Valheim", 0)]
STATS = {
    "aaaa1111": {"current_state": "running", "is_suspended": False, "resources": {
        "memory_bytes": 1073741824, "cpu_absolute": 42.5, "disk_bytes": 5368709120, "network_rx_bytes": 2048,
        "network_tx_bytes": 4096, "uptime": 7_500_000}},
    "bbbb2222": {"current_state": "offline", "is_suspended": False, "resources": {"memory_bytes": 0, "cpu_absolute": 0}},
    "cccc3333": {"current_state": "running", "is_suspended": False, "resources": {"memory_bytes": 524288000, "cpu_absolute": 7}},
}


class FakePanel(BaseHTTPRequestHandler):
    """Enough of the Pelican Client API to drive the bot's real urllib code path."""

    requests = []
    fail_power = False
    per_page_seen = []

    def log_message(self, *_args):
        pass

    def _send(self, code, payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self):
        FakePanel.requests.append((self.command, self.path, self.headers.get("authorization")))
        if self.headers.get("authorization") != f"Bearer {KEY}":
            self._send(401, {"errors": [{"detail": "bad key"}]})
            return False
        return True

    def do_GET(self):
        if not self._authorised():
            return
        path = self.path.split("?")[0]
        if path == "/api/client":
            self._send(200, {"object": "list", "data": SERVERS, "meta": {"pagination": {"total_pages": 1}}})
        elif path == "/api/client/redirect":
            self._send(302, headers={"location": "http://127.0.0.1:1/steal"})
        elif path.endswith("/resources") and path.split("/")[-2] in STATS:
            self._send(200, {"object": "stats", "attributes": STATS[path.split("/")[-2]]})
        else:
            self._send(404, {})

    def do_POST(self):
        if not self._authorised():
            return
        length = int(self.headers.get("content-length", 0))
        FakePanel.requests.append(("BODY", self.path, json.loads(self.rfile.read(length))))
        self._send(500 if FakePanel.fail_power else 204)


_httpd = HTTPServer(("127.0.0.1", 0), FakePanel)
threading.Thread(target=_httpd.serve_forever, daemon=True).start()
PANEL_URL = f"http://127.0.0.1:{_httpd.server_port}"

MEMBERS = [
    {"id": "u-ops", "username": "ops", "display_name": "Ops", "is_bot": False, "is_webhook": False, "role_ids": ["r-ops"]},
    {"id": "u-admin", "username": "root", "display_name": "Root", "is_bot": False, "is_webhook": False, "role_ids": ["r-admin"]},
    {"id": "u-plain", "username": "plain", "display_name": "Plain", "is_bot": False, "is_webhook": False, "role_ids": []},
]
ROLE_DEFS = [
    {"id": "r-everyone", "name": "@everyone", "permissions": 0, "is_everyone": True},
    {"id": "r-ops", "name": "ops", "permissions": 0, "is_everyone": False},
    {"id": "r-admin", "name": "admin", "permissions": 1, "is_everyone": False},
]


def message(author_id, content):
    return {"id": "m1", "author_id": author_id, "channel_id": "c1", "content": content}


def reset_cooldowns():
    for command in pelican.bot.commands.values():
        if command.cooldown is not None:
            command.cooldown._last = {}


def setup(*, confirm=True):
    core.PELICAN_URL = PANEL_URL
    core.PELICAN_API_KEY = KEY
    core.PELICAN_SERVERS = []
    core.PELICAN_LOG_CHANNEL = "log1"
    FakePanel.requests = []
    FakePanel.fail_power = False
    pelican._last_status_text = None
    reset_cooldowns()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.respond("GET", "/roles", ROLE_DEFS)
    pelican.bot.client = client
    pelican.bot.space = Space(client)
    pelican.bot.authors = AuthorFilter(client, space=pelican.bot.space, ignore_bots=True)
    pelican.bot.me_id = "bot-1"
    asyncio.run(pelican.bot.space.refresh_roles())
    asyncio.run(pelican.bot.space.refresh_members())
    confirmations = []

    async def fake_confirm(self, prompt, *, timeout=30):
        confirmations.append(prompt)
        return confirm

    Context.confirm = fake_confirm
    client.confirmations = confirmations
    return client


def process(client, author, content):
    asyncio.run(pelican.bot.process_message(message(author, content)))


def last(client):
    return client.sent[-1]["content"]


def power_posts():
    return [r[2] for r in FakePanel.requests if r[0] == "BODY"]


def test_servers_lists_state_cpu_and_ram():
    client = setup()
    process(client, "u-plain", "!servers")
    reply = last(client)
    assert "[up] **Minecraft** - running - cpu " in reply
    assert "1.0 GiB / 2048 MiB" in reply
    assert "[down] **Minecraft Modded** - offline" in reply
    assert "500.0 MiB (no limit)" in reply


def test_server_detail_shows_disk_and_uptime():
    client = setup()
    process(client, "u-plain", "!server valheim")
    reply = last(client)
    assert "**Valheim**" in reply and "node-1" in reply
    reset_cooldowns()
    process(client, "u-plain", "!server aaaa1111")
    assert "disk 5.0 GiB, uptime 2h 5m" in last(client)


class prefixed:
    """Runs a block with the bot answering to `?`, the way `SLIMM_PREFIX` would set it."""

    def __enter__(self):
        self.original, pelican.bot.prefix = pelican.bot.prefix, "?"

    def __exit__(self, *_exc):
        pelican.bot.prefix = self.original


def test_no_match_reply_names_the_configured_prefix():
    client = setup()
    with prefixed():
        process(client, "u-plain", "?server nothing")
    assert "try `?servers`" in last(client)


def test_server_name_ambiguous_and_missing():
    client = setup()
    process(client, "u-plain", "!server mine")
    assert "more than one server" in last(client)
    client.sent.clear()
    reset_cooldowns()
    process(client, "u-plain", "!server nothing")
    assert "no server matches" in last(client)


def test_allowed_servers_filter_hides_the_rest():
    client = setup()
    core.PELICAN_SERVERS = ["valheim", "bbbb2222"]
    process(client, "u-plain", "!servers")
    reply = last(client)
    assert "Valheim" in reply and "Modded" in reply and "**Minecraft** " not in reply


def test_power_denied_without_role_or_permission():
    client = setup()
    process(client, "u-plain", "!start valheim")
    assert "power commands need the control role or MANAGE_SERVER" in last(client)
    assert power_posts() == [] and client.sent[-1]["channel_id"] == "c1"


def test_power_by_role_holder_logs_then_acts():
    client = setup()
    process(client, "u-ops", "!start valheim")
    assert power_posts() == [{"signal": "start"}]
    assert "sent `start` to `Valheim`" in last(client)
    audit = [m for m in client.sent if m["channel_id"] == "log1"]
    assert len(audit) == 1 and "`start` on `Valheim` by @ops (u-ops) - requested" in audit[0]["content"]


def test_administrator_may_restart_and_asks_to_confirm():
    client = setup()
    process(client, "u-admin", "!restart aaaa1111")
    assert client.confirmations == ["restart `Minecraft`?"], (client.confirmations, [m["content"] for m in client.sent])
    assert power_posts() == [{"signal": "restart"}]


def test_declined_confirmation_sends_nothing():
    client = setup(confirm=False)
    process(client, "u-ops", "!stop valheim")
    assert power_posts() == [] and "cancelled" in last(client)
    assert [m for m in client.sent if m["channel_id"] == "log1"] == []


def test_start_needs_no_confirmation():
    client = setup()
    process(client, "u-ops", "!start valheim")
    assert client.confirmations == []


def test_power_refused_when_no_log_channel_is_configured():
    client = setup()
    core.PELICAN_LOG_CHANNEL = ""
    process(client, "u-ops", "!start valheim")
    assert "PELICAN_LOG_CHANNEL" in last(client) and power_posts() == []


def test_power_refused_when_the_audit_line_cannot_be_written():
    client = setup()
    client.respond("POST", "/channels/log1/messages", ApiError(403, "forbidden"))
    process(client, "u-ops", "!start valheim")
    assert power_posts() == [] and "won't run this" in last(client)


def test_failed_power_is_reported_and_logged():
    client = setup()
    FakePanel.fail_power = True
    process(client, "u-ops", "!start valheim")
    assert "failed: the panel answered HTTP 500" in last(client)
    assert "failed (the panel answered HTTP 500)" in [m for m in client.sent if m["channel_id"] == "log1"][-1]["content"]


def test_power_on_a_server_outside_the_filter_is_not_found():
    client = setup()
    core.PELICAN_SERVERS = ["valheim"]
    process(client, "u-ops", "!start minecraft")
    assert "no server matches" in last(client) and power_posts() == []


def test_key_is_sent_as_bearer_and_never_echoed():
    client = setup()
    process(client, "u-plain", "!servers")
    assert all(auth == f"Bearer {KEY}" for _, _, auth in FakePanel.requests if auth)
    core.PELICAN_API_KEY = "wrong"
    reset_cooldowns()
    process(client, "u-plain", "!servers")
    assert "refused the API key" in last(client)
    assert all(KEY not in m["content"] and "wrong" not in m["content"] for m in client.sent)


def test_redirects_are_not_followed_with_the_key():
    setup()
    try:
        core.api_get("/redirect")
        raise AssertionError("expected PelicanError")
    except core.PelicanError as err:
        assert "302" in str(err)


def test_unreachable_panel_is_a_readable_error():
    client = setup()
    core.PELICAN_URL = "http://127.0.0.1:1"
    process(client, "u-plain", "!servers")
    assert "can't reach the panel: the panel could not be reached" in last(client)


def test_url_must_be_https_unless_lan_http_is_allowed():
    assert core.url_problem("http://panel.lan", False) and not core.url_problem("http://panel.lan", True)
    assert not core.url_problem("https://panel.example", False)
    assert core.url_problem("https://user:pw@panel.example", True)
    assert core.url_problem("ftp://panel.example", True)
    assert core.permission_problem("NOPE") and core.permission_problem("NONE")
    assert core.permission_problem("MANAGE_SERVER") is None


def test_status_message_is_created_then_edited_in_place_without_spam():
    client = setup()
    client.respond("PATCH", f"/channels/status1/messages/{pelican.status_message_id()}", ApiError(404, "not found"))
    asyncio.run(pelican.status_once())
    created = [m for m in client.sent if m["channel_id"] == "status1"]
    assert len(created) == 1 and "**Game servers**" in created[0]["content"]
    edits_before = len([c for c in client.calls if c[0] == "PATCH"])
    client.respond("PATCH", f"/channels/status1/messages/{pelican.status_message_id()}", None)
    asyncio.run(pelican.status_once())
    assert len([m for m in client.sent if m["channel_id"] == "status1"]) == 1
    assert len([c for c in client.calls if c[0] == "PATCH"]) == edits_before

    STATS["cccc3333"] = {**STATS["cccc3333"], "current_state": "offline"}
    try:
        asyncio.run(pelican.status_once())
    finally:
        STATS["cccc3333"]["current_state"] = "running"
    patches = [c for c in client.calls if c[0] == "PATCH"]
    assert len(patches) == edits_before + 1 and "[down] **Valheim**" in patches[-1][2]["content"]
    assert len([m for m in client.sent if m["channel_id"] == "status1"]) == 1


def test_status_interval_has_a_floor():
    assert core.PELICAN_STATUS_INTERVAL >= core.MIN_STATUS_INTERVAL_SECONDS


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
