# slimbots

A discord.py-shaped bot framework for slim-m: `Bot()`, `@bot.command`, `ctx`, and a live `Space` model, plus the safeguards seven real bots each used to rebuild by hand (bot-ignore, cooldowns, permission gates, clean lifecycle).

See [`../docs/framework.md`](../docs/framework.md) for the full shape and the reasoning behind it - async HTTP, identity, command registration, the embed seam.
`../bot-casino/` is the reference port built on it.

The pre-0.3 sync primitives are still here, unchanged, for the templates not yet ported to `Bot`:

- authentication (`Authorization: Bearer`, a real `User-Agent`, `GET /me`)
- a REST helper, `Client.call`, for any route a template needs, including a
  raw (non-JSON) body and extra headers for an attachment upload
- an idempotent `send` that keeps the message id fixed across a retry,
  retries only a genuinely uncertain failure - a network error, a 5xx, or a
  429 - never a rejected 4xx, and can carry `attachment_ids`
- the websocket connect and hello handshake (`Connection`)
- the reconnect loop with exponential backoff, and treating a `401` as
  terminal rather than retryable (`run_forever`)
- a `seq` cursor in sqlite and the `/sync` catch-up call (`cursor`)
- refusing to carry a token over plain `ws://` to anything but a loopback
  address (`socket_url`)

See `docs/bots/building-bots.md` in slim-m for the protocol underneath all
of this, and `bot-ping/` in this repo for that protocol written out with
nothing hidden - it stays free of this package on purpose, so there is
always one template that shows the whole thing in one file.

## Testing a bot built on this

`slimbots.testing` has two fakes that never touch the network: `FakeAsyncClient` for a `Bot` (drive it with `await bot.process_message(...)` to exercise argument conversion, cooldowns, permission gates and the bot-ignore default), and `FakeClient` for the pre-0.3 sync templates.
Both record every call in `.calls`, auto-answer a message send into `.sent`, and raise immediately on anything else the code under test calls that was not stubbed first with `respond(method, path, response_or_exception)` - so a forgotten stub fails the test loudly instead of hanging.

```python
from slimbots.testing import FakeAsyncClient

client = FakeAsyncClient(me_id="bot-1")
await bot.process_message({"author_id": "u1", "channel_id": "c1", "content": "!ping", "id": "m1"})
assert client.sent[0]["content"] == "pong"
```

Neither is imported by `slimbots/__init__.py`, so it costs nothing for a bot that never writes a test.
Neither is a mock of slim-m's own validation or concurrency - money-safety still needs a real sqlite connection and real threads racing it, the way `bot-casino/test_concurrency.py` does.

## Hand-written, not generated

slim-m's wire contract is `schema/openapi.yaml`, and
`crates/slimm-server/tests/openapi_contract.rs` already fails CI on drift, so
a generated client would stay honest automatically. This package is
hand-written anyway. What it wraps is not route-shaped: auth, a socket, and
a retry policy, not a set of typed request/response pairs. A generator
produces that badly, and slim-m already prefers hand-written DTOs and models
on both the server and the client for the same reason. If somebody wants a
fully typed route client later, generating one from `openapi.yaml` is a
reasonable project - it just is not this one.

## Installing

The distribution is named `slim-m` on PyPI; the import stays `slimbots`.

```
pip install slim-m
```

To run against unreleased changes, install from git instead:

```
pip install "slim-m @ git+https://github.com/NC1107/slim-bots.git@main#subdirectory=slimbots"
```

Each template's `requirements.txt` pins one of those two lines.

## Releasing

`.github/workflows/publish.yml` builds and uploads on a published GitHub
release, using PyPI trusted publishing - it exchanges the workflow's own
OIDC identity for an upload token, so there is no API token stored in this
repo. The publisher on PyPI must name this repository, `publish.yml`, and
the `pypi` environment, or the exchange is refused.

Bump `version` in `pyproject.toml`, then publish a release.

## Tests

```
pip install -e ".[dev]"
pytest
```

Covers the retry policy (which errors retry, which do not, and that the
message id and content never change across a retry), the reconnect loop's
backoff and its terminal handling of a 401, and `socket_url`'s loopback-only
refusal of plaintext `ws://`.
