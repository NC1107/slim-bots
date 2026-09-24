"""What a command handler receives: who sent it, where, and how to answer."""


class Context:
    """One invocation of one command."""

    def __init__(self, *, bot, message, author, channel_id, command=None, invoked_with=None, raw_args=""):
        self.bot = bot
        self.message = message
        self.author = author
        self.channel_id = channel_id
        self.command = command
        self.invoked_with = invoked_with
        self.raw_args = raw_args

    @property
    def channel(self):
        return self.bot.space.channels.get(self.channel_id)

    def _render(self, content, embed):
        """The embed seam: folds `embed` into `content` until a real API exists."""
        if embed is None:
            return content or ""
        rendered = embed.render_fallback()
        return f"{content}\n{rendered}" if content else rendered

    async def send(self, content=None, *, embed=None, channel_id=None):
        return await self.bot.client.send(channel_id or self.channel_id, self._render(content, embed))

    async def reply(self, content=None, *, embed=None):
        return await self.bot.client.send(
            self.channel_id, self._render(content, embed), reply_to_id=self.message.get("id")
        )
