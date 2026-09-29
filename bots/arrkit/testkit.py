"""Fakes and a harness for driving a `Bot` end to end with its outside service faked; used by every test here."""

import asyncio

from slimbots import Store
from slimbots.authors import AuthorFilter
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []},
    {"id": "u2", "username": "amy", "display_name": "Amy", "is_bot": False, "is_webhook": False, "role_ids": []},
]


class FakeApi:
    """Stands in for a core's `api`: routes by path, records every call, and can fail or refuse writes on demand."""

    def __init__(self, **routes):
        self.calls = []
        self.routes = routes
        self.fail_with = None
        self.refuse_posts = None
        self.post_result = {"id": 99}
        self.fallback = None

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if self.fail_with:
            raise self.fail_with
        if method == "POST":
            if self.refuse_posts:
                raise self.refuse_posts
            return self.post_result
        if path not in self.routes and self.fallback:
            return self.fallback(path, params)
        route = self.routes[path]
        return route(params) if callable(route) else route

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class Patched:
    """Swaps a module attribute for the length of a with-block."""

    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self.saved = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self.value

    def __exit__(self, *_exc):
        setattr(self.module, self.name, self.saved)


class Harness:
    """One bot under test: builds its fake client and space, and sends it messages and button presses."""

    def __init__(self, bot, init_db):
        self.bot = bot
        self.init_db = init_db

    def setup(self, members=None, roles=None):
        bot = self.bot
        bot.store = Store(":memory:", migrate=self.init_db)
        asyncio.run(bot.store.open())
        bot.channels = {"c1"}
        client = FakeAsyncClient(me_id="bot-1")
        client.respond("GET", "/members", members or MEMBERS)
        client.respond("GET", "/roles", roles or [])
        client.edited = []

        async def record_edit(channel_id, message_id, content):
            client.edited.append({"channel_id": channel_id, "message_id": message_id, "content": content})

        client.edit_message = record_edit
        bot.client = client
        bot.space = Space(client)
        bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
        bot.me_id = "bot-1"
        asyncio.run(bot.space.refresh_members())
        if roles:
            asyncio.run(bot.space.refresh_roles())
            asyncio.run(bot.space.refresh_members())
        return client

    @staticmethod
    def message(content, msg_id="m1", author="u1"):
        return {"id": msg_id, "author_id": author, "channel_id": "c1", "content": content}

    def process(self, *messages):
        async def run():
            for msg in messages:
                await self.bot.process_message(msg)

        asyncio.run(run())

    @staticmethod
    def press(custom_id, message_id, user_id="u1"):
        return {
            "type": "interaction.created", "interaction_id": f"i-{custom_id}", "channel_id": "c1", "message_id": message_id,
            "custom_id": custom_id, "user_id": user_id, "user_display_name": user_id, "created_at": 1,
        }

    async def settle(self):
        """Waits out the presses in flight, but not a chooser's five-minute expiry timer."""
        while True:
            pending = [t for t in self.bot._background_tasks if "pick-" not in t.get_name()]
            if not pending:
                return
            await asyncio.gather(*pending)

    def press_chooser(self, client, *presses):
        """Presses `(custom_id, user_id)` pairs in order on the newest message that carries buttons."""
        async def flow():
            for custom_id, user_id in presses:
                chooser = next(m for m in reversed(client.sent) if m.get("components"))
                await self.bot._handle_frame(self.press(custom_id, chooser["id"], user_id))
                await self.settle()

        asyncio.run(flow())


def newest_buttons(client):
    return next(m for m in reversed(client.sent) if m.get("components"))


def labels(sent):
    return [b["label"] for row in sent["components"] for b in row["buttons"]]
