#!/usr/bin/env python3
"""The notes condenser and message renderer on real release-please bodies; run directly: python3 test_notes.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
import notes  # noqa: E402

URL = "https://github.com/Slim-m-org/slim-m/releases/tag/client-v0.93.0"


def test_a_single_feature_loses_scope_pr_link_and_commit_link():
    text = notes.condense(fixtures.CLIENT_0_93_0)
    assert text.splitlines()[0] == "- hold to lift in the desktop rail, a slimm link that reaches the running linux app, and left anchored poll bars"


def test_features_and_fixes_become_one_flat_list_with_no_headings_or_links():
    lines = notes.condense(fixtures.CLIENT_0_93_0).splitlines()
    assert len(lines) == 4
    assert all(line.startswith("- ") for line in lines)
    joined = "\n".join(lines)
    for leftover in ("#", "http", "](", "**", "7c271ab"):
        assert leftover not in joined


def test_server_notes_keep_unscoped_bullets_whole():
    lines = notes.condense(fixtures.SERVER_0_81_0).splitlines()
    assert lines[0] == "- a typer who leaves mid-typing no longer leaves the indicator stuck"
    assert lines[2] == "- the totp contract script waits until mid step so a step edge cannot refuse its codes"


def test_client_0_92_0_reads_as_three_bullets():
    assert notes.condense(fixtures.CLIENT_0_92_0).splitlines() == [
        "- copy or save an image from a right-click or long-press menu",
        "- the update notice is a compact chip in the title bar",
        "- linking Spotify reports what happened, and the listening box shows cover art and its source",
    ]


def test_a_long_release_is_capped_at_eight_bullets_then_says_how_many_more():
    lines = notes.condense(fixtures.CLIENT_0_88_0).splitlines()
    assert len([line for line in lines if line.startswith("- ")]) == 8
    assert lines[-1] == "and 36 more"


def test_the_cap_counts_every_change_in_the_body():
    body_bullets = [line for line in fixtures.CLIENT_0_88_0.splitlines() if line.startswith("* ")]
    assert len(body_bullets) == 44


def test_the_whole_message_stays_under_the_character_cap_even_with_huge_bullets():
    body = "### Features\n\n" + "\n".join(f"* **client:** {i} {'word ' * 120}([#{i}](https://x/{i}))" for i in range(20))
    message = notes.render("client", "9.9.9", URL, body)
    assert len(message) < 1500
    assert message.splitlines()[-1].startswith("and ")


def test_one_bullet_is_trimmed_with_three_dots_not_an_em_dash():
    body = "### Features\n\n* **client:** " + "a" * 500
    line = notes.condense(body).splitlines()[0]
    assert line.endswith("...") and len(line) <= 202 and chr(0x2014) not in line


def test_a_body_with_no_bullets_renders_just_title_and_link():
    assert notes.render("server", "1.0.0", URL, "") == f"**server 1.0.0**\n{URL}"
    assert notes.render("server", "1.0.0", URL, None) == f"**server 1.0.0**\n{URL}"
    assert notes.render("server", "1.0.0", URL, "## [1.0.0](x) (2026-01-01)\n") == f"**server 1.0.0**\n{URL}"


def test_render_puts_title_then_url_then_the_list():
    message = notes.render("client", "0.93.0", URL, fixtures.CLIENT_0_93_0)
    head = message.splitlines()[:3]
    assert head == ["**client 0.93.0**", URL, ""]
    assert message.splitlines()[3].startswith("- hold to lift")


def test_inline_links_keep_their_text_and_repeats_collapse():
    body = "### Bug Fixes\n\n* fix [the thing](https://x.y/z) now ([a1b2c3d](https://x))\n* fix [the thing](https://x.y/z) now ([a1b2c3d](https://x))\n"
    assert notes.condense(body) == "- fix the thing now"


def test_multi_scope_markup_is_stripped():
    assert notes.condense("* **server,client:** rotate it ([1b09059](https://x/1b09059))") == "- rotate it"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
