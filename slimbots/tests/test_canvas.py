from slimbots import Canvas
from slimbots.testing import FakeAsyncClient


async def test_place_posts_to_canvas_objects():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 1})
    canvas = Canvas(client, "c1")
    result = await canvas.place("note", x=0, y=0, w=220, h=140, props={"text": "hi"})
    assert result == {"id": "o1", "seq": 1}
    assert client.calls[-1][1] == "/channels/c1/canvas/objects"
    assert client.calls[-1][2]["kind"] == "note"
    assert client.calls[-1][2]["props"] == {"text": "hi"}


async def test_move_posts_a_move_op():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.move("o1", x=10, y=20)
    body = client.calls[-1][2]
    assert body["kind"] == "move"
    assert body["object_id"] == "o1"
    assert body["x"] == 10
    assert body["y"] == 20


async def test_remove_posts_a_remove_op_with_every_id():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.remove(["o1", "o2"])
    body = client.calls[-1][2]
    assert body["kind"] == "remove"
    assert body["object_ids"] == ["o1", "o2"]


async def test_viewport_sends_the_rectangle_as_query_params():
    client = FakeAsyncClient()
    client.respond("GET", "/channels/c1/canvas/objects", {"objects": []})
    canvas = Canvas(client, "c1")
    await canvas.viewport(min_x=0, min_y=0, max_x=100, max_y=100)
    method, path, body, params = client.calls[-1]
    assert method == "GET"
    assert path == "/channels/c1/canvas/objects"
    assert params == {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100, "limit": 100}
