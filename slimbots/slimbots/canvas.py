"""A channel's Voice Canvas: place/move/remove an object, and read a viewport; see docs/framework.md."""

import uuid


class Canvas:
    """Thin wrapper over one channel's `canvas/objects`/`canvas/ops` REST routes and its two live gateway signals."""

    def __init__(self, client, channel_id, *, bot=None):
        self._client = client
        self.channel_id = channel_id
        self._bot = bot

    async def place(self, kind, *, x, y, w, h, props=None, object_id=None):
        body = {"id": object_id or str(uuid.uuid4()), "kind": kind, "x": x, "y": y, "w": w, "h": h}
        if props is not None:
            body["props"] = props
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/objects", body)

    async def move(self, object_id, *, x, y, w=None, h=None, op_id=None):
        body = {"id": op_id or str(uuid.uuid4()), "kind": "move", "object_id": object_id, "x": x, "y": y}
        if w is not None:
            body["w"] = w
        if h is not None:
            body["h"] = h
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def remove(self, object_ids, *, op_id=None):
        body = {"id": op_id or str(uuid.uuid4()), "kind": "remove", "object_ids": list(object_ids)}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def clear(self, before_seq, *, op_id=None):
        """Removes every live object placed at or below `before_seq`; needs MANAGE_CANVAS unconditionally."""
        body = {"id": op_id or str(uuid.uuid4()), "kind": "clear", "before_seq": before_seq}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def restore(self, target_op, *, op_id=None):
        """Un-deletes exactly what a prior `remove` or `clear` op (`target_op`, its id) touched."""
        body = {"id": op_id or str(uuid.uuid4()), "kind": "restore", "target_op": target_op}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def reorder(self, object_id, z_index, *, op_id=None):
        """Sets one live object's stacking order to an explicit `z_index` - the caller computes the target."""
        body = {"id": op_id or str(uuid.uuid4()), "kind": "reorder", "object_id": object_id, "z_index": z_index}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def viewport(self, *, min_x, min_y, max_x, max_y, limit=100):
        """One page of objects in a rectangle - a bot's own reconciliation ground truth; see docs/framework.md."""
        params = {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y, "limit": limit}
        return await self._client.call("GET", f"/channels/{self.channel_id}/canvas/objects", params=params)

    async def send_cursor(self, x, y):
        """A live pointer position over the gateway; no ack and no history, it just stops arriving when you stop."""
        await self._bot_or_raise().send_frame({"type": "canvas.cursor", "channel_id": self.channel_id, "x": x, "y": y})

    async def send_stroke_preview(self, object_id, points, *, ended=False):
        """An in-flight drawing stroke over the gateway; send it repeatedly as it grows, `ended=True` on the last one."""
        await self._bot_or_raise().send_frame({
            "type": "canvas.stroke_preview", "channel_id": self.channel_id,
            "object_id": object_id, "points": list(points), "ended": ended,
        })

    def _bot_or_raise(self):
        if self._bot is None:
            raise RuntimeError("Canvas(..., bot=...) needs a bot to send a live gateway signal")
        return self._bot
