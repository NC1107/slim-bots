"""A message embed (decision 0030): the real `RequestEmbed` wire shape, with a text fallback for an older server."""

MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELDS = 25
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR_NAME = 256


class Embed:
    """One embed; `to_wire()` is what a bot sends, `render_fallback()` is what an older server sees instead."""

    def __init__(self, *, title=None, description=None, color=None, footer=None, url=None, timestamp=None):
        self.title = title
        self.description = description
        self.color = color
        self.footer_text = footer
        self.url = url
        self.timestamp = timestamp
        self.author = None
        self.image_url = None
        self.thumbnail_url = None
        self.fields = []

    def set_author(self, name, *, url=None):
        self.author = {"name": name[:MAX_AUTHOR_NAME], **({"url": url} if url else {})}
        return self

    def set_footer(self, text):
        self.footer_text = text
        return self

    def set_image(self, url):
        self.image_url = url
        return self

    def set_thumbnail(self, url):
        self.thumbnail_url = url
        return self

    def add_field(self, name, value, *, inline=False):
        if len(self.fields) < MAX_FIELDS:
            self.fields.append({"name": name[:MAX_FIELD_NAME], "value": value[:MAX_FIELD_VALUE], "inline": inline})
        return self

    def to_wire(self):
        """The `RequestEmbed` JSON body decision 0030 defines."""
        body = {}
        if self.title:
            body["title"] = self.title[:MAX_TITLE]
        if self.description:
            body["description"] = self.description[:MAX_DESCRIPTION]
        if self.url:
            body["url"] = self.url
        if self.color is not None:
            body["color"] = self.color
        if self.author:
            body["author"] = self.author
        if self.fields:
            body["fields"] = self.fields
        if self.footer_text:
            body["footer"] = {"text": self.footer_text[:MAX_FOOTER]}
        if self.timestamp is not None:
            body["timestamp"] = self.timestamp
        if self.image_url:
            body["image"] = {"url": self.image_url}
        if self.thumbnail_url:
            body["thumbnail"] = {"url": self.thumbnail_url}
        return body

    def render_fallback(self):
        """A markdown rendering, used as the message's own `content` (never blank) and as the whole reply if the server rejects `embeds`."""
        lines = []
        if self.title:
            title = f"**{self.title}**"
            lines.append(f"[{title}]({self.url})" if self.url else title)
        if self.description:
            lines.append(self.description)
        for field in self.fields:
            lines.append(f"**{field['name']}:** {field['value']}")
        if self.footer_text:
            lines.append(f"_{self.footer_text}_")
        return "\n".join(lines) or "(embed)"
