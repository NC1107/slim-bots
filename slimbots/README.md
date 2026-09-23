# slimbots

Shared plumbing for slim-m bot templates in this repo.

This is not a typed model of slim-m's API. There is no `Channel` class, no
`Message` object, no cache, no event hierarchy - the kind of thing a
discord.js-shaped SDK would give you for a few hundred endpoints. slim-m's
entire bot surface is: log in with a token, call REST, hold one websocket,
track a per-scope `seq`. That is what this package covers:

- authentication (`Authorization: Bearer`, a real `User-Agent`, `GET /me`)
- a REST helper, `Client.call`, for any route a template needs
- an idempotent `send` that keeps the message id fixed across a retry, and
  retries only a genuinely uncertain failure - a network error, a 5xx, or a
  429 - never a rejected 4xx
- the websocket connect and hello handshake (`Connection`)
- the reconnect loop with exponential backoff, and treating a `401` as
  terminal rather than retryable (`run_forever`)
- a `seq` cursor in sqlite and the `/sync` catch-up call (`cursor`)
- refusing to carry a token over plain `ws://` to anything but a loopback
  address (`socket_url`)

It deliberately does **not** give you a typed route client, a cache, or an
event-object hierarchy. See `docs/bots/building-bots.md` in slim-m for the
protocol this wraps, and `bot-ping/` in this repo for the same protocol
written out with nothing hidden - it stays free of this package on purpose,
so there is always one template that shows the whole thing in one file.

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
