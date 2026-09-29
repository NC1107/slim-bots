#!/usr/bin/env python3
"""Track/URL tests plus command-layer tests against a fake voice room and a scripted ffmpeg; run: python3 test_bot.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as music  # noqa: E402
import music_cog  # noqa: E402
import music_core  # noqa: E402
import player  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient, FakeAudioSource, FakeVoice, FakeVoiceSession  # noqa: E402
from slimbots.voice import VoiceError  # noqa: E402

REAL_BUILD_ARGS = player.build_ffmpeg_args
PUBLIC_URL = "https://93.184.216.34/stream.mp3"
USER = {"is_bot": False, "is_webhook": False, "role_ids": []}
MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", **USER},
    {"id": "u2", "username": "sam", "display_name": "Sam", **USER},
]
VOICE_CHANNELS = [
    Channel({"id": "v1", "name": "voice-room", "kind": "voice"}),
    Channel({"id": "v2", "name": "other-room", "kind": "voice"}),
]


class FakeRtc:
    """Just the `livekit.rtc` surface `publish_microphone` and the pump touch."""

    class TrackSource:
        SOURCE_MICROPHONE = "microphone"
        SOURCE_SCREENSHARE_AUDIO = "screenshare_audio"

    class AudioEncoding:
        def __init__(self, max_bitrate):
            self.max_bitrate = max_bitrate

    class TrackPublishOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class AudioFrame:
        def __init__(self, data, sample_rate, num_channels, samples_per_channel):
            self.data = data

    class LocalAudioTrack:
        @staticmethod
        def create_audio_track(name, source):
            return {"name": name, "source": source}

    AudioSource = FakeAudioSource

    def __init__(self):
        self.AudioSource = lambda sample_rate, num_channels: FakeAudioSource()


class FakeLocalParticipant:
    def __init__(self):
        self.published = []

    async def publish_track(self, track, options):
        self.published.append((track, options))


class FakeRoom:
    def __init__(self):
        self.local_participant = FakeLocalParticipant()


class MusicVoiceSession(FakeVoiceSession):
    def __init__(self, channel_id, *, can_publish=True):
        super().__init__(channel_id, can_publish=can_publish)
        self.rtc = FakeRtc()
        self.room = FakeRoom()


class MusicVoice(FakeVoice):
    async def join(self, channel_id):
        if self.join_error is not None:
            raise self.join_error
        session = MusicVoiceSession(channel_id, can_publish=self.can_publish)
        self.sessions.append(session)
        return session


def scripted_ffmpeg(track):
    """A stand-in decoder: `long` in the url never ends on its own, anything else emits 3 chunks and stops."""
    if "long" in track.url:
        code = f"import sys,time\nwhile True:\n    sys.stdout.buffer.write(b'\\x01'*{player.CHUNK_BYTES}); sys.stdout.flush(); time.sleep(0.01)"
    else:
        code = f"import sys\nsys.stdout.buffer.write(b'\\x01'*{player.CHUNK_BYTES * 3})"
    return [sys.executable, "-c", code]


def message(content, author="u1", msg_id="m1"):
    return {"id": msg_id, "author_id": author, "channel_id": "c1", "content": content}


def setup(*, member_channels=None, can_publish=True, join_error=None, jellyfin=False):
    music.bot.store = Store(":memory:")
    asyncio.run(music.bot.store.open())
    music.bot.channels = {"c1"}
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    music.bot.client = client
    music.bot.space = Space(client)
    music.bot.authors = AuthorFilter(client, space=music.bot.space, ignore_bots=True)
    music.bot.me_id = "bot-1"
    asyncio.run(music.bot.space.refresh_members())
    for channel in VOICE_CHANNELS:
        music.bot.space.channels[channel.id] = channel
    channels = {"u1": "v1"} if member_channels is None else member_channels
    music.bot.voice = MusicVoice(can_publish=can_publish, member_channels=channels, join_error=join_error)
    music_cog.active_sessions().clear()
    music_core.JELLYFIN_URL = "https://jf.example" if jellyfin else ""
    music_core.JELLYFIN_API_KEY = "key" if jellyfin else ""
    player.build_ffmpeg_args = scripted_ffmpeg
    return client


def scenario(client, *steps):
    """Runs `steps` (each an awaitable factory, or a chat line) inside one loop so background tasks survive."""

    async def run():
        for step in steps:
            if isinstance(step, str):
                await music.bot.process_message(message(step))
            elif isinstance(step, tuple):
                await music.bot.process_message(message(step[1], author=step[0]))
            else:
                await step()
        for session in list(music_cog.active_sessions().values()):
            await session.stop(reason="test over")

    asyncio.run(run())


async def until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for the condition"
        await asyncio.sleep(0.01)


def replies(client):
    return [m["content"] for m in client.sent]


def test_stream_url_detection_and_naming():
    assert music_core.is_stream_url("https://radio.example/live.mp3")
    assert not music_core.is_stream_url("file:///etc/passwd")
    assert not music_core.is_stream_url("ftp://host/x.mp3")
    assert not music_core.is_stream_url("daft punk around the world")
    assert music_core.track_from_url("https://radio.example/a%20song.mp3").title == "a song.mp3"
    assert music_core.track_from_url("https://radio.example/").title == "radio.example"


def test_private_addresses_are_refused_but_the_jellyfin_host_is_not():
    setup(jellyfin=True)
    assert "private" in music_core.check_stream_url("http://127.0.0.1/x.mp3")
    assert "private" in music_core.check_stream_url("http://10.0.0.5:8096/x.mp3")
    assert "private" in music_core.check_stream_url("http://169.254.169.254/latest")
    assert music_core.check_stream_url(PUBLIC_URL) is None
    original = music_core.JELLYFIN_URL
    music_core.JELLYFIN_URL = "http://10.0.0.5:8096"
    try:
        assert music_core.check_stream_url("http://10.0.0.5:8096/Audio/1/stream") is None
    finally:
        music_core.JELLYFIN_URL = original


def test_jellyfin_track_carries_the_auth_header_and_a_direct_url_does_not():
    setup(jellyfin=True)
    track = music_core.track_from_item({"Id": "a1", "Name": "Song", "AlbumArtist": "Band", "RunTimeTicks": 1_800_000_000})
    assert track.title == "Band - Song" and track.duration_seconds == 180
    assert "/Audio/a1/stream" in track.url and "Static=true" in track.url
    assert track.headers == 'Authorization: MediaBrowser Token="key"\r\n'
    assert music_core.track_from_url(PUBLIC_URL).headers is None


def test_ffmpeg_args_whitelist_protocols_and_scope_the_header_to_jellyfin():
    player.shutil.which = lambda name: "/usr/bin/ffmpeg"
    args = REAL_BUILD_ARGS(music_core.Track("t", "https://jf.example/Audio/1/stream", headers="Authorization: x\r\n"))
    assert args[args.index("-protocol_whitelist") + 1] == "http,https,tcp,tls,crypto"
    assert "-headers" in args and args[-1] == "pipe:1"
    assert "-headers" not in REAL_BUILD_ARGS(music_core.Track("t", PUBLIC_URL))


def test_play_requires_the_invoker_to_be_in_a_call():
    client = setup(member_channels={})
    scenario(client, f"~play {PUBLIC_URL}")
    assert replies(client)[-1] == "join a voice channel first, then run `play` again."
    assert not music.bot.voice.sessions


def test_play_a_name_without_a_music_library_asks_for_a_url():
    client = setup()
    scenario(client, "~play daft punk")
    assert "direct `http(s)` stream url" in replies(client)[-1]
    assert not music.bot.voice.sessions


def test_play_refuses_a_private_url():
    client = setup()
    scenario(client, "~play http://127.0.0.1:8096/x.mp3")
    assert "private network" in replies(client)[-1]
    assert not music.bot.voice.sessions


def test_play_searches_jellyfin_with_the_whole_multiword_query():
    client = setup(jellyfin=True)
    seen = []
    original = music_core.search_tracks
    music_core.search_tracks = lambda query, limit=5: seen.append(query) or [{"Id": "a1", "Name": "Around the World", "AlbumArtist": "Daft Punk"}]
    try:
        scenario(client, "~play daft punk around the world")
    finally:
        music_core.search_tracks = original
    assert seen == ["daft punk around the world"]
    assert replies(client)[0] == "playing **Daft Punk - Around the World** in #voice-room."


def test_play_plays_into_the_invokers_call_as_a_microphone_track_and_leaves_when_done():
    client = setup()

    async def finished():
        await until(lambda: music.bot.voice.sessions[0].left)

    scenario(client, f"~play {PUBLIC_URL}", finished)
    voice_session = music.bot.voice.sessions[0]
    assert voice_session.channel_id == "v1"
    assert replies(client)[0] == "playing **stream.mp3** in #voice-room."
    track, options = voice_session.room.local_participant.published[0]
    assert track["name"] == "music" and options.source == "microphone"
    assert options.audio_encoding.max_bitrate == player.AUDIO_MAX_BITRATE
    assert track["source"].frame_count == 3
    assert "queue finished, leaving the call." in replies(client)
    assert music_cog.active_sessions() == {}


def test_a_second_play_queues_and_a_skip_keeps_the_queue():
    client = setup()

    async def playing_long():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)

    async def on_second():
        await until(lambda: music_cog.active_sessions()["v1"].current.url == PUBLIC_URL)

    scenario(
        client, "~play https://93.184.216.34/long.mp3", playing_long, f"~play {PUBLIC_URL}", "~queue", "~skip", on_second,
    )
    said = replies(client)
    assert "queued **stream.mp3** (#1 up next)." in said
    assert "now: **long.mp3**\n1. stream.mp3" in said
    assert "skipped **long.mp3**." in said
    assert "now playing **stream.mp3**." in said


def test_each_voice_channel_gets_its_own_queue():
    client = setup(member_channels={"u1": "v1", "u2": "v2"})

    async def both_playing():
        await until(lambda: len(music_cog.active_sessions()) == 2)

    scenario(
        client, "~play https://93.184.216.34/long.mp3", ("u2", "~play https://93.184.216.34/long2.mp3"), both_playing,
    )
    assert sorted(s.channel_id for s in music.bot.voice.sessions) == ["v1", "v2"]
    assert len(music.bot.voice.sessions) == 2


def test_stop_clears_the_queue_and_leaves():
    client = setup()

    async def playing():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)

    scenario(client, "~play https://93.184.216.34/long.mp3", playing, f"~play {PUBLIC_URL}", "~stop")
    assert music.bot.voice.sessions[0].left
    assert music_cog.active_sessions() == {}
    assert "music stopped (stopped by Nick)." in replies(client)


def test_controls_answer_only_for_someone_in_the_playing_call():
    client = setup(member_channels={"u1": "v1", "u2": "v2"})

    async def playing():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)

    scenario(client, "~play https://93.184.216.34/long.mp3", playing, ("u2", "~skip"), ("u2", "~stop"))
    assert replies(client).count("nothing is playing in your call.") == 2
    assert "music stopped (test over)." in replies(client)
    assert [s.channel_id for s in music.bot.voice.sessions] == ["v1"]


def test_pause_and_resume_report_state_and_np_shows_the_track():
    client = setup()

    async def playing():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)

    scenario(client, "~play https://93.184.216.34/long.mp3", playing, "~pause", "~pause", "~resume", "~resume", "~np")
    said = replies(client)
    assert said.count("paused.") == 1 and said.count("already paused.") == 1
    assert said.count("resumed.") == 1 and said.count("already playing.") == 1
    embeds = [m["embeds"][0] for m in client.sent if m.get("embeds")]
    assert [e["title"] for e in embeds] == ["long.mp3"]


def test_an_empty_call_makes_the_bot_leave():
    client = setup()
    client.respond("GET", "/channels/v1/voice/roster", {"participants": [{"user_id": "bot-1"}]})

    async def playing_then_empty():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)
        music_cog.active_sessions()["v1"].wake_monitor()
        await until(lambda: music.bot.voice.sessions[0].left)

    scenario(client, "~play https://93.184.216.34/long.mp3", playing_then_empty)
    assert "music stopped (the call is empty)." in replies(client)
    assert music_cog.active_sessions() == {}


def test_play_refuses_when_the_bot_cannot_speak():
    client = setup(can_publish=False)
    scenario(client, f"~play {PUBLIC_URL}")
    assert "can't speak there" in replies(client)[-1]
    assert music.bot.voice.sessions[0].left
    assert music_cog.active_sessions() == {}


def test_play_names_the_missing_permission_when_the_bot_cannot_connect():
    client = setup(join_error=VoiceError("needs VIEW_CHANNEL and CONNECT in that channel"))
    scenario(client, f"~play {PUBLIC_URL}")
    assert replies(client)[-1] == "can't join #voice-room: needs VIEW_CHANNEL and CONNECT in that channel"


def test_a_full_queue_is_refused():
    client = setup()
    original = music_core.MAX_QUEUE_LENGTH
    music_core.MAX_QUEUE_LENGTH = 1

    async def playing():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)

    try:
        scenario(client, "~play https://93.184.216.34/long.mp3", playing, f"~play {PUBLIC_URL}", f"~play {PUBLIC_URL}")
    finally:
        music_core.MAX_QUEUE_LENGTH = original
    assert "the queue is full (1 tracks)." in replies(client)


def test_config_needs_both_jellyfin_settings_or_neither():
    setup()
    assert music_core.check_config() is None
    music_core.JELLYFIN_URL = "https://jf.example"
    assert "both" in music_core.check_config()
    music_core.JELLYFIN_API_KEY = "key"
    assert music_core.check_config() is None
    music_core.JELLYFIN_URL = "http://jf.example"
    assert "plaintext" in music_core.check_config()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
