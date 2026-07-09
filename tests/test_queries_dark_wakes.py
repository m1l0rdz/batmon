import json

from batmond import db
from batmon_web import queries


def _conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))


def _set_feed(conn, feed):
    from batmond.db import set_state
    set_state(conn, "dark_wakes", json.dumps(feed))


def test_dark_wakes_reads_feed(tmp_path):
    conn = _conn(tmp_path)
    _set_feed(conn, [{"ts": 1, "reason": "wifibt", "duration_sec": 3600,
                      "wh_drained": 6.8,
                      "culprits": [{"proc": "MSTeams", "why": "kept-awake", "n": 2}]}])
    items = queries.dark_wakes(conn)
    assert len(items) == 1
    assert items[0]["culprits"][0]["proc"] == "MSTeams"


def test_dark_wakes_empty_when_no_feed(tmp_path):
    conn = _conn(tmp_path)
    assert queries.dark_wakes(conn) == []


def test_frequent_culprit_repeats(tmp_path):
    conn = _conn(tmp_path)
    _set_feed(conn, [
        {"ts": 3, "reason": "a", "duration_sec": 1, "wh_drained": 5.0,
         "culprits": [{"proc": "MSTeams", "why": "kept-awake", "n": 1}]},
        {"ts": 2, "reason": "b", "duration_sec": 1, "wh_drained": 5.0,
         "culprits": [{"proc": "MSTeams", "why": "kept-awake", "n": 1}]},
        {"ts": 1, "reason": "c", "duration_sec": 1, "wh_drained": 5.0,
         "culprits": [{"proc": "calaccessd", "why": "woke", "n": 1}]},
    ])
    fc = queries.frequent_culprit(conn)
    assert fc == {"proc": "MSTeams", "n": 2}


def test_frequent_culprit_none_when_no_repeat(tmp_path):
    conn = _conn(tmp_path)
    _set_feed(conn, [
        {"ts": 1, "reason": "a", "duration_sec": 1, "wh_drained": 5.0,
         "culprits": [{"proc": "MSTeams", "why": "kept-awake", "n": 1}]}])
    assert queries.frequent_culprit(conn) is None
