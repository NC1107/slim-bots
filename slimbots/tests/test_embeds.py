from slimbots import Embed
from slimbots.context import Context
from slimbots.http import ApiError
from slimbots.testing import FakeAsyncClient


def test_to_wire_only_includes_fields_that_were_set():
    embed = Embed(title="Hand", description="you win").add_field("stake", "10", inline=True)
    wire = embed.to_wire()
    assert wire == {"title": "Hand", "description": "you win", "fields": [{"name": "stake", "value": "10", "inline": True}]}


def test_to_wire_nests_footer_author_image_thumbnail():
    embed = Embed(footer="a footer").set_author("bot", url="https://x").set_image("https://img").set_thumbnail("https://thumb")
    wire = embed.to_wire()
    assert wire["footer"] == {"text": "a footer"}
    assert wire["author"] == {"name": "bot", "url": "https://x"}
    assert wire["image"] == {"url": "https://img"}
    assert wire["thumbnail"] == {"url": "https://thumb"}


def test_fields_are_capped_at_25():
    embed = Embed()
    for i in range(30):
        embed.add_field(str(i), str(i))
    assert len(embed.fields) == 25


def test_render_fallback_is_never_blank():
    assert Embed().render_fallback() == "(embed)"


async def test_send_with_embed_posts_the_real_wire_shape():
    client = FakeAsyncClient()
    embed = Embed(title="Balance").add_field("chips", "100")
    await client.send("c1", "", embeds=[embed.to_wire()], fallback_content=embed.render_fallback())
    sent = client.sent[-1]
    assert sent["embeds"] == [embed.to_wire()]


async def test_send_falls_back_to_plain_text_when_the_server_rejects_embeds():
    client = FakeAsyncClient()
    attempts = {"n": 0}

    def reject_the_first_attempt_only():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ApiError(400, {"error": "unknown field embeds"})
        return {"id": "m1", "seq": 1}

    client.respond("POST", "/channels/c1/messages", reject_the_first_attempt_only)
    embed = Embed(title="Balance").add_field("chips", "100")
    await client.send("c1", "Balance", embeds=[embed.to_wire()], fallback_content="Balance\nfallback text")

    assert attempts["n"] == 2
    assert client.calls[-1][2]["content"] == "Balance\nfallback text"
    assert "embeds" not in client.calls[-1][2]


def _ctx():
    class FakeBot:
        pass

    bot = FakeBot()
    bot.client = FakeAsyncClient()
    return Context(bot=bot, message={"id": "m1"}, author=None, channel_id="c1"), bot.client


async def test_context_reply_with_only_an_embed_never_sends_blank_content():
    ctx, client = _ctx()
    await ctx.reply(embed=Embed(title="Balance").add_field("chips", "100"))
    content = client.calls[-1][2]["content"]
    assert isinstance(content, str) and content != ""
    assert "Balance" in content
    assert client.calls[-1][2]["embeds"]


async def test_context_reply_with_text_and_embed_keeps_the_embed_out_of_content():
    ctx, client = _ctx()
    await ctx.reply("here you go", embed=Embed(title="Balance"))
    assert client.calls[-1][2]["content"] == "here you go"
