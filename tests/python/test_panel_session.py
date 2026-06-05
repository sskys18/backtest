"""P0 — KRX session tagger tests (UTC ms → KST session enum).

KST is constant UTC+9 (no DST). KRX cash hours 09:00-15:30 KST;
premarket 08:00-09:00; close 15:20-15:30.
"""

from __future__ import annotations

from bt.panel.session import tag_session

# Session enum: 0 closed, 1 premarket, 2 open, 3 mid, 4 close, 5 overnight


def _utc_ms(y, mo, d, h, mi):
    import datetime as _dt

    return int(
        _dt.datetime(y, mo, d, h, mi, tzinfo=_dt.timezone.utc).timestamp() * 1000
    )


def test_session_tagger_kst_open():
    # 00:30Z == 09:30 KST → open
    assert tag_session(_utc_ms(2026, 6, 4, 0, 30), session_aware=True) == 2
    # 23:00Z (prev day) == 08:00 KST → premarket
    assert tag_session(_utc_ms(2026, 6, 3, 23, 0), session_aware=True) == 1
    # daily mode → unchanged (always open)
    assert tag_session(_utc_ms(2026, 6, 3, 23, 0), session_aware=False) == 2
