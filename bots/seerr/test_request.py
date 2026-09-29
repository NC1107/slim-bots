#!/usr/bin/env python3
"""The `!request` chooser and the Approve/Decline buttons: who may press, and what reaches Seerr; run directly: python3 test_request.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import approvals  # noqa: E402
import picker  # noqa: E402
import seerr_core as core  # noqa: E402
from test_bot import FakeSeerr, Patched, message, request, seerr, setup  # noqa: E402

RESULTS = [
    {"mediaType": "movie", "id": 10, "title": "Dune", "releaseDate": "2021-09-15", "mediaInfo": {"status": 5}},
    {"mediaType": "movie", "id": 11, "title": "Dune Part Two", "releaseDate": "2024-02-27"},
    {"mediaType": "tv", "id": 12, "name": "Dune: Prophecy", "firstAirDate": "2024-11-17", "mediaInfo": {"status": 2}},
    {"mediaType": "tv", "id": 13, "name": "Dune Show", "firstAirDate": "2030-01-01"},
]


def press(custom_id, message_id, user_id="u1"):
    return {
        "type": "interaction.created", "interaction_id": f"i-{custom_id}", "channel_id": "c1", "message_id": message_id,
        "custom_id": custom_id, "user_id": user_id, "user_display_name": user_id, "created_at": 1,
    }


def chooser(client):
    return next(m for m in reversed(client.sent) if m.get("components"))


def labels(sent):
    return [b["label"] for row in sent["components"] for b in row["buttons"]]


async def settle():
    while True:
        pending = [t for t in seerr.bot._background_tasks if not t.get_name().startswith("seerr-pick-")]
        if not pending:
            return
        await asyncio.gather(*pending)


def linked_setup(fake):
    client = setup()
    picker._picks.clear()
    asyncio.run(seerr.bot.store.run(core.set_link, "u1", 5, "amy"))
    fake.search_results = RESULTS
    return client


def run_request(fake, *presses):
    """Runs `!request dune`, then each `(custom_id, user_id)` press on the chooser."""
    client = linked_setup(fake)

    async def flow():
        await seerr.bot.process_message(message("!request dune"))
        for custom_id, user_id in presses:
            await seerr.bot._handle_frame(press(custom_id, chooser(client)["id"], user_id))
            await settle()

    with Patched(fake):
        asyncio.run(flow())
    return client


def test_the_chooser_leaves_out_what_is_already_requested_or_available():
    client = run_request(FakeSeerr())
    assert labels(chooser(client)) == ["Dune Part Two (2024) [movie]", "Dune Show (2030) [show]", "Cancel"]
    assert "2 already requested or available" in chooser(client)["content"]


def test_choosing_a_movie_files_it_as_the_linked_user():
    fake = FakeSeerr()
    client = run_request(fake, (f"{picker.ID_PREFIX}sel:0", "u1"))
    method, path, _params, body = next(c for c in fake.calls if c[0] == "POST")
    assert (method, path, body) == ("POST", "/request", {"mediaType": "movie", "mediaId": 11, "userId": 5})
    assert "requested **Dune Part Two (2024) [movie]**" in client.edited[-1]["content"]


def test_choosing_a_show_asks_for_all_seasons():
    fake = FakeSeerr()
    run_request(fake, (f"{picker.ID_PREFIX}sel:1", "u1"))
    body = next(c for c in fake.calls if c[0] == "POST")[3]
    assert body == {"mediaType": "tv", "mediaId": 13, "seasons": "all", "userId": 5}


def test_another_member_pressing_is_told_no_and_nothing_is_requested():
    fake = FakeSeerr()
    client = run_request(fake, (f"{picker.ID_PREFIX}sel:0", "u2"))
    assert not any(c[0] == "POST" for c in fake.calls)
    assert "only Nick can choose" in client.ephemerals[-1]["content"]


def test_cancel_and_a_repeat_press_never_file_twice():
    fake = FakeSeerr()
    client = run_request(fake, (f"{picker.ID_PREFIX}sel:0", "u1"), (f"{picker.ID_PREFIX}sel:0", "u1"))
    assert len([c for c in fake.calls if c[0] == "POST"]) == 1
    assert "expired" in client.ephemerals[-1]["content"]
    cancelled = run_request(FakeSeerr(), (f"{picker.ID_PREFIX}cancel", "u1"))
    assert cancelled.edited[-1]["content"] == "cancelled."


def test_an_expired_chooser_refuses_and_closes():
    fake = FakeSeerr()
    client = linked_setup(fake)

    async def flow():
        await seerr.bot.process_message(message("!request dune"))
        next(iter(picker._picks.values())).created_at -= picker.EXPIRES_SECONDS + 1
        await seerr.bot._handle_frame(press(f"{picker.ID_PREFIX}sel:0", chooser(client)["id"]))
        await settle()

    with Patched(fake):
        asyncio.run(flow())
    assert not any(c[0] == "POST" for c in fake.calls) and "timed out" in client.edited[-1]["content"]


def test_seerr_refusing_the_request_is_reported_in_the_chooser():
    fake = FakeSeerr()
    fake.refuse_posts = OSError("409")
    client = run_request(fake, (f"{picker.ID_PREFIX}sel:0", "u1"))
    assert "could not request **Dune Part Two (2024) [movie]**" in client.edited[-1]["content"]


def test_a_request_with_only_available_matches_opens_no_chooser():
    fake = FakeSeerr()
    client = linked_setup(fake)
    fake.search_results = RESULTS[:1]
    with Patched(fake):
        asyncio.run(seerr.bot.process_message(message("!request dune")))
    assert "already requested or available" in client.sent[-1]["content"] and not client.sent[-1].get("components")


def test_people_in_search_results_are_never_offered():
    fake = FakeSeerr()
    fake.search_results = [{"mediaType": "person", "id": 1, "name": "Dune Actor"}, RESULTS[1]]
    assert [r["id"] for r in _search(fake)] == [11]


def _search(fake):
    with Patched(fake):
        return core.search("dune")


def approval_setup(admin_ids=("u1",), with_roles=True):
    """A pending request post on screen; `admin_ids` hold a role carrying the approver permission."""
    client = setup()
    members = [{"id": m["id"], "username": m["username"], "display_name": m["display_name"], "is_bot": False, "is_webhook": False,
                "role_ids": ["r-admin"] if m["id"] in admin_ids else []} for m in ({"id": "u1", "username": "nick", "display_name": "Nick"}, {"id": "u2", "username": "amy", "display_name": "Amy"})]
    client.respond("GET", "/members", members)
    if with_roles:
        client.respond("GET", "/roles", [{"id": "r-admin", "name": "admin", "permissions": 1 << 15}, {"id": "r-all", "name": "everyone", "permissions": 4, "is_everyone": True}])
    asyncio.run(seerr.bot.space.refresh_roles()) if with_roles else None
    asyncio.run(seerr.bot.space.refresh_members())
    fake = FakeSeerr()
    fake.requests = [request(1, status=2, media_status=5)]
    with Patched(fake):
        asyncio.run(seerr.poll_once())
        fake.requests.append(request(7, tmdb=7))
        asyncio.run(seerr.poll_once())
    return client, fake


def decide(client, fake, action, user_id):
    async def flow():
        await seerr.bot._handle_frame(press(f"{approvals.ID_PREFIX}{action}:7", chooser(client)["id"], user_id))
        await settle()

    with Patched(fake):
        asyncio.run(flow())


def test_a_member_with_the_permission_can_approve_and_the_post_is_closed_out():
    client, fake = approval_setup()
    decide(client, fake, "approve", "u1")
    assert ("POST", "/request/7/approve", {}, None) in [(m, p, q or {}, b) for m, p, q, b in fake.calls]
    assert "Approved by u1." in client.edited[-1]["content"] and "Requested:" in client.edited[-1]["content"]
    assert client.component_edits[-1]["components"] == []


def test_the_approval_made_from_the_button_is_not_announced_again_by_the_poll():
    client, fake = approval_setup()
    decide(client, fake, "approve", "u1")
    fake.requests[-1]["status"] = 2
    sent_before = len(client.sent)
    with Patched(fake):
        asyncio.run(seerr.poll_once())
    assert len(client.sent) == sent_before


def test_decline_calls_the_decline_route():
    client, fake = approval_setup()
    decide(client, fake, "decline", "u1")
    assert any(c[:2] == ("POST", "/request/7/decline") for c in fake.calls)
    assert "Declined by u1." in client.edited[-1]["content"]


def test_a_member_without_the_permission_is_refused_and_nothing_changes():
    client, fake = approval_setup()
    decide(client, fake, "approve", "u2")
    assert not any(c[1].startswith("/request/7/") for c in fake.calls)
    assert "only members with MANAGE_SERVER" in client.ephemerals[-1]["content"]


def test_a_bot_that_cannot_read_roles_says_what_it_needs_and_approves_nothing():
    client, fake = approval_setup(with_roles=False)
    decide(client, fake, "approve", "u1")
    assert not any(c[1].startswith("/request/7/") for c in fake.calls)
    assert "MANAGE_ROLES" in client.ephemerals[-1]["content"]


def test_seerr_failing_the_approval_leaves_the_buttons_and_says_so():
    client, fake = approval_setup()
    fake.refuse_posts = OSError("500")
    decide(client, fake, "approve", "u1")
    assert "request 7 is unchanged" in client.ephemerals[-1]["content"]
    assert client.component_edits == []


def test_a_malformed_decision_id_is_ignored():
    client, fake = approval_setup()

    async def flow():
        await seerr.bot._handle_frame(press(f"{approvals.ID_PREFIX}approve:abc", chooser(client)["id"]))
        await seerr.bot._handle_frame(press(f"{approvals.ID_PREFIX}nuke:7", chooser(client)["id"]))
        await settle()

    with Patched(fake):
        asyncio.run(flow())
    assert not any(c[0] == "POST" for c in fake.calls)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
