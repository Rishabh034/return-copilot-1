"""The single source of "now" for anything snapshotted, persisted, or billed.

`datetime.now()` scattered through the code makes two things impossible: a
byte-exact snapshot of what we send Anthropic (the system prompt carries the
current IST date, so it changes daily) and a deterministic assertion on a
persisted `message_time`.

Everything that needs the wall clock for an *observable* value goes through
here so tests can freeze it. Call these as attributes (`clock.now_utc()`), not
`from app.clock import now_utc` — a direct import binds the function object and
a monkeypatch on this module would not be seen.

Deliberately not used for logging timestamps or SQLAlchemy column defaults:
those are never asserted on, and routing them through a patchable hook would
let a frozen clock leak into rows that should record real time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_utc() -> datetime:
    """Current time, UTC, timezone-aware."""
    return datetime.now(timezone.utc)


def now_ist() -> datetime:
    """Current time in Asia/Kolkata — the timezone the agent reasons about dates in."""
    return now_utc().astimezone(IST)
