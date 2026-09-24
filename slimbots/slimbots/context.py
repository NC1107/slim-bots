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

    def _prepare(self, content, embed):
        """Body content (never blank - slim-m refuses that) and the fallback text for a server that rejects `embeds`."""
        if embed is None:
            return content or "", None, None
        rendered = embed.render_fallback()
        body_content = content if content else rendered
        fallback = f"{content}\n{rendered}" if content else rendered
        return body_content, [embed.to_wire()], fallback

    async def send(self, content=None, *, embed=None, channel_id=None):
        body_content, embeds, fallback = self._prepare(content, embed)
        return await self.bot.client.send(channel_id or self.channel_id, body_content, embeds=embeds, fallback_content=fallback)

    async def reply(self, content=None, *, embed=None):
        body_content, embeds, fallback = self._prepare(content, embed)
        return await self.bot.client.send(
            self.channel_id, body_content, reply_to_id=self.message.get("id"), embeds=embeds, fallback_content=fallback
        )
