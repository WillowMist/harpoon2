"""Lock-in test for the Block B requeue cooldown prefix in check_stalled_transfers.

The cooldown prevents the 20s-tick requeue race that was producing a
120 GB torrent stuck oscillating at 25 GB (one batch's cumulative
file_size), forever. Block B's marker write and the cooldown filter
MUST use the same literal prefix — otherwise the cooldown silently
breaks and the race returns.

Behavioral coverage was attempted but proved too fragile to mock
reliably (check_stalled_transfers has too many overlapping queryset
chains). The source-level contract below is the durable check.
"""
import inspect
import re

from itemqueue import tasks as tasks_module


def test_cooldown_filter_prefix_matches_marker_write():
    """The cooldown's details__startswith and the marker must use the
    same literal prefix. If a future refactor changes one without the
    other, the cooldown silently breaks and Block B's requeue race returns."""
    src = inspect.getsource(tasks_module.check_stalled_transfers)

    # The cooldown filter call: details__startswith='Requeued by check_stalled_transfers'
    cooldown_idx = src.find("details__startswith=")
    assert cooldown_idx != -1, "cooldown's details__startswith filter not found"
    cooldown_call = src[cooldown_idx:cooldown_idx + 300]
    m = re.search(r"details__startswith=['\"]([^'\"]+)['\"]", cooldown_call)
    assert m, "could not extract cooldown prefix"
    cooldown_prefix = m.group(1)

    # The marker write must use the same prefix literal
    assert cooldown_prefix in src, (
        f"cooldown prefix {cooldown_prefix!r} not found anywhere in check_stalled_transfers"
    )
    # And specifically: there's a marker write that uses this exact prefix
    assert f"details='{cooldown_prefix}" in src or f'details="{cooldown_prefix}' in src, (
        f"marker write using cooldown prefix {cooldown_prefix!r} not found"
    )


def test_cooldown_window_is_5_minutes():
    """The cooldown window must match the stall_threshold (5 min) so the
    same operator-visible interval applies to both signals. Locked in."""
    src = inspect.getsource(tasks_module.check_stalled_transfers)
    # Look for the cooldown's timedelta
    assert "timedelta(minutes=5)" in src, \
        "cooldown must use timedelta(minutes=5) to match stall_threshold"
