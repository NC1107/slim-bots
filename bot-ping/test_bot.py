#!/usr/bin/env python3
"""Stdlib-only tests for bot.py's message-filtering logic.

No live deployment and no `websockets` import needed: these tests exercise
`should_answer` and `is_bot_or_webhook` directly, monkeypatching `bot.call` so
a GET /users/{id} lookup never leaves the process. bot-ping stays library-free
on purpose, so its tests do too - see bot.py's own docstring.
"""

import unittest
from unittest import mock

import bot


class ShouldAnswerTests(unittest.TestCase):
    def setUp(self):
        bot._is_bot_cache.clear()

    def test_ignores_its_own_messages(self):
        message = {"author_id": "me", "content": "!ping"}
        self.assertFalse(bot.should_answer(message, "me"))

    def test_ignores_another_bot(self):
        with mock.patch.object(bot, "call", return_value={"is_bot": True}):
            message = {"author_id": "other-bot", "content": "!ping"}
            self.assertFalse(bot.should_answer(message, "me"))

    def test_ignores_a_webhook(self):
        with mock.patch.object(bot, "call", return_value={"is_webhook": True}):
            message = {"author_id": "some-webhook", "content": "!ping"}
            self.assertFalse(bot.should_answer(message, "me"))

    def test_answers_a_person(self):
        with mock.patch.object(bot, "call", return_value={"is_bot": False}) as called:
            message = {"author_id": "a-person", "content": "!ping"}
            self.assertTrue(bot.should_answer(message, "me"))
            called.assert_called_once_with("GET", "/users/a-person")

    def test_ignores_non_trigger_content(self):
        with mock.patch.object(bot, "call", return_value={"is_bot": False}):
            message = {"author_id": "a-person", "content": "not a ping"}
            self.assertFalse(bot.should_answer(message, "me"))

    def test_is_bot_lookup_is_cached(self):
        with mock.patch.object(bot, "call", return_value={"is_bot": False}) as called:
            bot.is_bot_or_webhook("a-person")
            bot.is_bot_or_webhook("a-person")
            called.assert_called_once()

    def test_a_failed_lookup_reads_as_not_a_bot(self):
        with mock.patch.object(bot, "call", side_effect=RuntimeError("gone")):
            self.assertFalse(bot.is_bot_or_webhook("deleted-user"))


if __name__ == "__main__":
    unittest.main()
