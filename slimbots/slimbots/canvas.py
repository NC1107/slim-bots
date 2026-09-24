"""A channel's Voice Canvas: place/move/remove an object, and read a viewport; see docs/framework.md."""

import uuid


class Canvas:
    """Thin wrapper over one channel's `canvas/objects` and `canvas/ops` routes."""

    def __init__(self, client, channel_id):
        self._client = client
        self.channel_id = channel_id

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

    async def viewport(self, *, min_x, min_y, max_x, max_y, limit=100):
        """One page of objects in a rectangle - a bot's own reconciliation ground truth; see docs/framework.md."""
        params = {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y, "limit": limit}
        return await self._client.call("GET", f"/channels/{self.channel_id}/canvas/objects", params=params)
