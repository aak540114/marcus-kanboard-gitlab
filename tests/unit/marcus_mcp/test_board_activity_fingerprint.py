"""Unit tests for _board_activity_fingerprint (live-refresh change signal)."""

from src.marcus_mcp.server import _board_activity_fingerprint


def _t(tid, col, mod):
    return {"id": tid, "column_id": col, "date_modification": mod}


def test_stable_for_same_state():
    tasks = [_t(1, 2, 100), _t(2, 3, 200)]
    assert _board_activity_fingerprint(tasks) == _board_activity_fingerprint(tasks)


def test_order_independent():
    a = [_t(1, 2, 100), _t(2, 3, 200)]
    b = [_t(2, 3, 200), _t(1, 2, 100)]
    assert _board_activity_fingerprint(a) == _board_activity_fingerprint(b)


def test_changes_on_column_move():
    before = [_t(1, 2, 100)]
    after = [_t(1, 5, 100)]  # moved to a different column
    assert _board_activity_fingerprint(before) != _board_activity_fingerprint(after)


def test_changes_on_modification_time():
    before = [_t(1, 2, 100)]
    after = [_t(1, 2, 101)]  # edited → date_modification bumped
    assert _board_activity_fingerprint(before) != _board_activity_fingerprint(after)


def test_empty_is_stable():
    assert _board_activity_fingerprint([]) == _board_activity_fingerprint([])
