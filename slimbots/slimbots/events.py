"""Small typed payloads for the frame kinds `Bot._handle_frame` dispatches by name; see docs/framework.md."""


class MessageEdited:
    """A message's content changed; `.message` is the raw, updated message dict."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.seq = frame["seq"]
        self.op_seq = frame.get("op_seq")
        self.message = frame["message"]


class MessageDeleted:
    """A message was soft-deleted."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.message_id = frame["message_id"]
        self.op_seq = frame.get("op_seq")


class ReactionsChanged:
    """A message's reaction tally changed; `.reactions` is `[{"emoji": ..., "count": ...}]`."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.message_id = frame["message_id"]
        self.reactions = frame["reactions"]


class ThreadUpdated:
    """A message's thread gained a reply, or was opened for the first time."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.parent_message_id = frame["parent_message_id"]
        self.thread_channel_id = frame["thread_channel_id"]
        self.reply_count = frame["reply_count"]
        self.last_reply_at = frame.get("last_reply_at")


class MessagePinned:
    """A message was pinned in its channel."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.message_id = frame["message_id"]
        self.pinned_by = frame.get("pinned_by")
        self.pinned_at = frame["pinned_at"]


class MessageUnpinned:
    """A message was unpinned."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.message_id = frame["message_id"]


class PollVoted:
    """A poll's tally changed; `.options` is `[{"position": ..., "votes": ...}]`, never who voted."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.message_id = frame["message_id"]
        self.options = frame["options"]


class PresenceChanged:
    """A member's online/away/offline status changed."""

    def __init__(self, frame):
        self.user_id = frame["user_id"]
        self.status = frame["status"]


class ProfileChanged:
    """A member's profile (name, avatar) changed; refetch if it matters."""

    def __init__(self, frame):
        self.user_id = frame["user_id"]


class TypingStarted:
    """A member started typing in a channel; slim-m sends no explicit stop, it lapses on its own."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.user_id = frame["user_id"]


class TypingStopped:
    """A typing indicator lapsed or was explicitly cleared."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.user_id = frame["user_id"]


class ChannelCreated:
    """A channel was created; `.channel` is the raw channel dict."""

    def __init__(self, frame):
        self.channel = frame["channel"]


class ChannelUpdated:
    """A channel's settings changed."""

    def __init__(self, frame):
        self.channel = frame["channel"]


class ChannelDeleted:
    """A channel was deleted."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]


class OverwriteChanged:
    """A channel's permission overwrites changed."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]


class CategoryChanged:
    """A category was created, renamed, reordered, or deleted; carries no fields of its own."""

    def __init__(self, frame):
        pass


class VoiceActivityChanged:
    """A channel's voice activity level changed (someone is speaking, or stopped)."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]


class CallRinging:
    """A DM call ring started."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.ring_id = frame["ring_id"]
        self.caller_id = frame["caller_id"]


class CallRingEnded:
    """A DM call ring ended; `.outcome` is one of answered/declined/canceled/timed_out."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.ring_id = frame["ring_id"]
        self.outcome = frame["outcome"]


class CanvasObjectsRestored:
    """A prior remove or clear was undone; `.object_ids` is what came back."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.seq = frame["seq"]
        self.op_id = frame["op_id"]
        self.object_ids = frame["object_ids"]


class CanvasCursorMoved:
    """A live pointer position on a channel's canvas; no "stop" frame, it just stops arriving."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.user_id = frame["user_id"]
        self.x = frame["x"]
        self.y = frame["y"]


class CanvasStrokePreviewUpdated:
    """An in-flight drawing stroke on a channel's canvas; `.ended` is true on the final update."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.user_id = frame["user_id"]
        self.object_id = frame["object_id"]
        self.points = frame["points"]
        self.ended = frame["ended"]


class CanvasObjectMoved:
    """A canvas object was moved or resized (the same op either way)."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.seq = frame["seq"]
        self.op_id = frame["op_id"]
        self.object_id = frame["object_id"]
        self.x = frame["x"]
        self.y = frame["y"]
        self.w = frame["w"]
        self.h = frame["h"]


class CanvasObjectReordered:
    """A canvas object's stacking order (z_index) changed."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.seq = frame["seq"]
        self.op_id = frame["op_id"]
        self.object_id = frame["object_id"]
        self.z_index = frame["z_index"]


class CanvasMediaSlotChanged:
    """A camera/screen-share media slot on the canvas moved, resized, locked, or changed owner."""

    def __init__(self, frame):
        self.channel_id = frame["channel_id"]
        self.kind = frame["kind"]
        self.user_id = frame["user_id"]
        self.x = frame["x"]
        self.y = frame["y"]
        self.w = frame["w"]
        self.h = frame["h"]
        self.locked = frame["locked"]
        self.sent_to_back = frame["sent_to_back"]


class ReportsChanged:
    """The moderation report queue changed; carries no fields (MANAGE_MESSAGES only)."""

    def __init__(self, frame):
        pass
