"""The `embed=` seam: renders as text until slim-m has a real embed API; see docs/framework.md."""


class Embed:
    """A structured message body, sent as text until slim-m has a real embed API."""

    def __init__(self, *, title=None, description=None, color=None, footer=None, url=None):
        self.title = title
        self.description = description
        self.color = color
        self.footer = footer
        self.url = url
        self.fields = []

    def add_field(self, name, value, *, inline=False):
        self.fields.append((name, value, inline))
        return self

    def render_fallback(self):
        """A markdown rendering used until a real embed field exists on the wire."""
        lines = []
        if self.title:
            title = f"**{self.title}**"
            lines.append(f"[{title}]({self.url})" if self.url else title)
        if self.description:
            lines.append(self.description)
        for name, value, _inline in self.fields:
            lines.append(f"**{name}:** {value}")
        if self.footer:
            lines.append(f"_{self.footer}_")
        return "\n".join(lines)
