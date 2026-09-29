"""Real-livekit suite: run only in the `voice-real-livekit` CI job, never in the fast fake-based one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
