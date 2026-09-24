from slimbots.authors import AuthorFilter
from slimbots.testing import FakeClient


def test_ignores_self():
    client = FakeClient(me_id="bot-1")
    authors = AuthorFilter(client)
    assert authors.should_handle("bot-1", "bot-1") is False


def test_ignores_none_author():
    client = FakeClient()
    authors = AuthorFilter(client)
    assert authors.should_handle(None, "bot-1") is False
    assert authors.should_handle("", "bot-1") is False


def test_ignores_another_bot_by_default():
    client = FakeClient(me_id="bot-1")
    client.respond("GET", "/users/bot-2", {"id": "bot-2", "is_bot": True, "is_webhook": False})
    authors = AuthorFilter(client)
    assert authors.should_handle("bot-2", "bot-1") is False


def test_ignores_a_webhook_by_default():
    client = FakeClient(me_id="bot-1")
    client.respond("GET", "/users/hook-1", {"id": "hook-1", "is_bot": False, "is_webhook": True})
    authors = AuthorFilter(client)
    assert authors.should_handle("hook-1", "bot-1") is False


def test_handles_a_human_author():
    client = FakeClient(me_id="bot-1")
    client.respond("GET", "/users/u1", {"id": "u1", "is_bot": False, "is_webhook": False})
    authors = AuthorFilter(client)
    assert authors.should_handle("u1", "bot-1") is True


def test_caches_the_lookup_per_author():
    client = FakeClient(me_id="bot-1")
    client.respond("GET", "/users/bot-2", {"id": "bot-2", "is_bot": True})
    authors = AuthorFilter(client)
    authors.should_handle("bot-2", "bot-1")
    authors.should_handle("bot-2", "bot-1")
    lookups = [c for c in client.calls if c[1] == "/users/bot-2"]
    assert len(lookups) == 1


def test_opt_out_lets_a_bot_hear_other_bots():
    client = FakeClient(me_id="bot-1")
    client.respond("GET", "/users/bot-2", {"id": "bot-2", "is_bot": True})
    authors = AuthorFilter(client, ignore_bots=False)
    assert authors.should_handle("bot-2", "bot-1") is True


def test_failed_lookup_is_treated_as_human_and_not_cached():
    class FlakyClient(FakeClient):
        def __init__(self):
            super().__init__(me_id="bot-1")
            self.attempts = 0

        def call(self, method, path, body=None, *, raw_body=None, headers=None):
            if path == "/users/u1":
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("network hiccup")
                return {"id": "u1", "is_bot": False}
            return super().call(method, path, body, raw_body=raw_body, headers=headers)

    client = FlakyClient()
    authors = AuthorFilter(client)
    assert authors.should_handle("u1", "bot-1") is True
    assert authors.should_handle("u1", "bot-1") is True
    assert client.attempts == 2
