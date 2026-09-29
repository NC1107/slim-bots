#!/usr/bin/env python3
"""`!request` as a linked user, and the Approve/Decline buttons: who may press, and what reaches Seerr; run directly: python3 test_request.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import approvals  # noqa: E402
import seerr_cog  # noqa: E402
import seerr_core as core  # noqa: E402
from arrkit.testkit import Patched, labels, newest_buttons  # noqa: E402
from test_bot import harness, request, seerr, seerr_api, setup  # noqa: E402

RESULTS = [
    {"mediaType": "movie", "id": 10, "title": "Dune", "releaseDate": "2021-09-15", "mediaInfo": {"status": 5}},
    {"mediaType": "movie", "id": 11, "title": "Dune Part Two", "releaseDate": "2024-02-27"},
    {"mediaType": "tv", "id": 12, "name": "Dune: Prophecy", "firstAirDate": "2024-11-17", "mediaInfo": {"status": 2}},
    {"mediaType": "tv", "id": 13, "name": "Dune Show", "firstAirDate": "2030-01-01"},
    {"mediaType": "person", "id": 14, "name": "Dune Actor"},
]


def request_flow(api, *presses, text="!request dune"):
    """A linked member runs a request command, then presses `(custom_id, user_id)` pairs on the chooser."""
    client = setup()
    asyncio.run(seerr.bot.store.run(core.set_link, "u1", 5, "amy"))
    api.search_results = RESULTS
    with Patched(core, "api", api):
        harness.process(harness.message(text))
        if presses:
            harness.press_chooser(client, *presses)
    return client


def test_the_chooser_leaves_out_people_and_what_is_already_requested_or_available_and_says_so():
    client = request_flow(seerr_api())
    sent = newest_buttons(client)
    assert labels(sent) == ["Dune Part Two (2024) [movie]", "Dune Show (2030) [show]", "Cancel"]
    assert "(2 already requested or available, left out)" in sent["content"]


def test_choosing_a_movie_files_it_as_the_linked_seerr_user_and_a_show_asks_for_all_seasons():
    api = seerr_api()
    client = request_flow(api, ("seerrpick:sel:0", "u1"))
    assert api.posts()[0][1:] == ("/request", None, {"mediaType": "movie", "mediaId": 11, "userId": 5})
    assert "requested **Dune Part Two (2024) [movie]**" in client.edited[-1]["content"]
    show_api = seerr_api()
    request_flow(show_api, ("seerrpick:sel:1", "u1"))
    assert show_api.posts()[0][3] == {"mediaType": "tv", "mediaId": 13, "seasons": "all", "userId": 5}


def test_seerr_refusing_the_request_is_reported_in_the_chooser():
    api = seerr_api()
    api.refuse_posts = OSError("409")
    client = request_flow(api, ("seerrpick:sel:0", "u1"))
    assert "could not request **Dune Part Two (2024) [movie]**" in client.edited[-1]["content"]


def test_when_only_available_matches_exist_no_chooser_opens():
    api = seerr_api()
    client = setup()
    asyncio.run(seerr.bot.store.run(core.set_link, "u1", 5, "amy"))
    api.search_results = RESULTS[:1]
    with Patched(core, "api", api):
        harness.process(harness.message("!request dune"))
    assert "already requested or available" in client.sent[-1]["content"] and not client.sent[-1].get("components")


def test_the_default_user_stands_in_for_a_member_who_has_not_linked():
    api = seerr_api()
    client = setup()
    core.SEERR_DEFAULT_USER_ID = 3
    try:
        api.search_results = RESULTS
        with Patched(core, "api", api):
            harness.process(harness.message("!request dune"))
            harness.press_chooser(client, ("seerrpick:sel:0", "u1"))
    finally:
        core.SEERR_DEFAULT_USER_ID = None
    assert api.posts()[0][3]["userId"] == 3


def approval_setup(admin_ids=("u1",), with_roles=True):
    """A pending request post on screen; `admin_ids` hold a role carrying the approver permission."""
    members = [
        {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": ["r-admin"] if "u1" in admin_ids else []},
        {"id": "u2", "username": "amy", "display_name": "Amy", "is_bot": False, "is_webhook": False, "role_ids": []},
    ]
    roles = [{"id": "r-admin", "name": "admin", "permissions": 1 << 15}, {"id": "r-all", "name": "everyone", "permissions": 4, "is_everyone": True}]
    seerr_cog.GUARD.cooldown._last.clear()
    client = harness.setup(members=members, roles=roles if with_roles else None)
    api = seerr_api()
    api.requests = [request(1, status=2, media_status=5)]
    with Patched(core, "api", api):
        asyncio.run(seerr.poll_once())
        api.requests.append(request(7, tmdb=7))
        asyncio.run(seerr.poll_once())
    return client, api


def decide(client, api, custom_id, user_id):
    with Patched(core, "api", api):
        harness.press_chooser(client, (custom_id, user_id))


def decisions(api):
    return [c for c in api.calls if c[0] == "POST" and c[1].startswith("/request/7/")]


def test_a_member_with_the_permission_approves_and_the_post_is_closed_out():
    client, api = approval_setup()
    decide(client, api, f"{approvals.ID_PREFIX}approve:7", "u1")
    assert [c[1] for c in decisions(api)] == ["/request/7/approve"]
    assert "Requested:" in client.edited[-1]["content"] and "Approved by u1." in client.edited[-1]["content"]
    assert client.component_edits[-1]["components"] == []


def test_the_approval_made_from_the_button_is_not_announced_again_by_the_poll():
    client, api = approval_setup()
    decide(client, api, f"{approvals.ID_PREFIX}approve:7", "u1")
    api.requests[-1]["status"] = 2
    sent_before = len(client.sent)
    with Patched(core, "api", api):
        asyncio.run(seerr.poll_once())
    assert len(client.sent) == sent_before


def test_decline_calls_the_decline_route():
    client, api = approval_setup()
    decide(client, api, f"{approvals.ID_PREFIX}decline:7", "u1")
    assert [c[1] for c in decisions(api)] == ["/request/7/decline"] and "Declined by u1." in client.edited[-1]["content"]


def test_a_member_without_the_permission_is_refused_and_nothing_changes():
    client, api = approval_setup()
    decide(client, api, f"{approvals.ID_PREFIX}approve:7", "u2")
    assert decisions(api) == [] and "only members with MANAGE_SERVER" in client.ephemerals[-1]["content"]


def test_a_bot_that_cannot_read_roles_says_what_it_needs_and_approves_nothing():
    client, api = approval_setup(with_roles=False)
    decide(client, api, f"{approvals.ID_PREFIX}approve:7", "u1")
    assert decisions(api) == [] and "MANAGE_ROLES" in client.ephemerals[-1]["content"]


def test_seerr_failing_the_approval_leaves_the_buttons_and_says_so():
    client, api = approval_setup()
    api.refuse_posts = OSError("500")
    decide(client, api, f"{approvals.ID_PREFIX}approve:7", "u1")
    assert "request 7 is unchanged" in client.ephemerals[-1]["content"] and client.component_edits == []


def test_a_malformed_decision_id_is_ignored():
    client, api = approval_setup()
    decide(client, api, f"{approvals.ID_PREFIX}approve:abc", "u1")
    decide(client, api, f"{approvals.ID_PREFIX}nuke:7", "u1")
    assert decisions(api) == []


USERS = [
    {"id": 5, "displayName": "amy", "jellyfinUsername": "Amy", "permissions": 32},
    {"id": 1, "displayName": "npc", "permissions": 2},
]


def link_setup():
    """u1 holds the approver permission, u2 does not; Seerr knows a normal user and an admin."""
    client = approval_setup()[0]
    api = seerr_api()
    api.users = USERS
    seerr_cog.GUARD.cooldown._last.clear()
    return client, api


def say(api, text, author="u1", msg_id="m1"):
    seerr_cog.GUARD.cooldown._last.clear()
    with Patched(core, "api", api):
        harness.process(harness.message(text, msg_id, author))


def link_of(member_id):
    return asyncio.run(seerr.bot.store.run(core.get_link, member_id))


def test_an_approver_links_another_member_by_any_seerr_name():
    client, api = link_setup()
    say(api, "!request link @amy AMY")
    assert link_of("u2")[0] == 5 and "linked @amy" in client.sent[-1]["content"]


def test_an_approver_may_link_an_admin_seerr_account():
    client, api = link_setup()
    say(api, "!request link @nick npc")
    assert link_of("u1")[0] == 1


def test_a_non_approver_cannot_link_themselves_or_anyone_else():
    client, api = link_setup()
    say(api, "!request link @amy amy", author="u2")
    say(api, "!request link @nick npc", author="u2", msg_id="m2")
    assert link_of("u2") is None and link_of("u1") is None
    assert client.ephemerals[-1]["content"].startswith("only members with MANAGE_SERVER can link or unlink")
    assert not any(c[1] == "/user" for c in api.calls)


def test_unlink_is_gated_the_same_way_and_removes_a_link_for_an_approver():
    client, api = link_setup()
    asyncio.run(seerr.bot.store.run(core.set_link, "u2", 5, "amy"))
    say(api, "!request unlink @amy", author="u2")
    assert link_of("u2") is not None
    say(api, "!request unlink @amy", msg_id="m2")
    assert link_of("u2") is None and "unlinked @amy" in client.sent[-1]["content"]


def test_one_seerr_user_links_to_at_most_one_member_and_unknown_names_are_reported():
    client, api = link_setup()
    say(api, "!request link @amy amy")
    say(api, "!request link @nick amy", msg_id="m2")
    assert link_of("u1") is None and "already linked to someone else" in client.sent[-1]["content"]
    say(api, "!request link @nick ghost", msg_id="m3")
    assert 'no seerr user called "ghost"' in client.sent[-1]["content"]


def test_link_without_a_member_or_with_an_unknown_one_says_how_to_use_it():
    client, api = link_setup()
    say(api, "!request link")
    say(api, "!request link @nobody amy", msg_id="m2")
    assert client.sent[-1]["content"].startswith("name a member first") and client.sent[-2]["content"].startswith("name a member first")


def test_a_bot_that_cannot_read_roles_refuses_link_commands_and_says_what_it_needs():
    client = harness.setup()
    api = seerr_api()
    api.users = USERS
    say(api, "!request link @amy amy")
    assert "MANAGE_ROLES" in client.ephemerals[-1]["content"] and link_of("u2") is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
