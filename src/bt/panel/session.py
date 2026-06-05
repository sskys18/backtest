"""KRX session tagger — map a UTC-ms timestamp to a session enum.

KST is a constant UTC+9 offset (Korea observes no DST), so the conversion is a
fixed +9h shift; no zoneinfo dependency required.

Session enum (u8):
    0 closed, 1 premarket, 2 open, 3 mid, 4 close, 5 overnight

KRX cash hours (KST):
    premarket 08:00-09:00
    open      09:00-15:30  (regular session)
    close     15:20-15:30  (closing auction window, a sub-band of open)
    overnight everything after close until premarket
"""

from __future__ import annotations

_KST_OFFSET_MS = 9 * 3600 * 1000


def tag_session(timestamp_ms: int, *, session_aware: bool) -> int:
    """Return the session enum for a UTC-millisecond timestamp.

    Daily / non-session-aware panels always return ``2`` (open) — there is no
    intraday session structure to express.

    Holidays are not fabricated here: callers that need holiday-aware tagging
    pass a curated holiday set to a higher layer. This function tags by
    time-of-day only (TODO: holiday set wiring once a curated calendar exists).
    """
    if not session_aware:
        return 2

    kst_ms = timestamp_ms + _KST_OFFSET_MS
    ms_in_day = kst_ms % (24 * 3600 * 1000)
    minutes = ms_in_day // 60000  # minutes since KST midnight

    pre_open = 8 * 60       # 08:00
    open_start = 9 * 60     # 09:00
    close_start = 15 * 60 + 20  # 15:20
    open_end = 15 * 60 + 30     # 15:30

    if pre_open <= minutes < open_start:
        return 1  # premarket
    if open_start <= minutes < close_start:
        return 2  # open
    if close_start <= minutes < open_end:
        return 4  # close (closing-auction band)
    return 5  # overnight (outside trading window)
