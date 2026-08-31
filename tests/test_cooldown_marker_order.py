"""Lock-in test for the Block B cooldown race fix.

The original code wrote the cooldown marker (ItemHistory.objects.create)
AFTER calling .delay(). The race: another Block B tick fires between
.delay() and .create(), sees no marker, re-requeues. Result: 14+ parallel
transfer_files_async tasks for the same item, each running concurrently,
starving the celery workers of slots for everything else.

The fix: write the marker BEFORE .delay(). This way the next Block B
tick (5 min later, gated by the marker check) sees the marker and skips
unconditionally.
"""
import inspect

from itemqueue.tasks import check_stalled_transfers


def test_cooldown_marker_written_before_delay():
    """The ItemHistory.objects.create call (the cooldown marker) must
    come BEFORE transfer_files_async.delay() in Block B's requeue
    branch. Otherwise a Block B tick can fire between .delay() and
    .create(), see no marker, and re-requeue — the race that left
    14+ duplicate transfer_files_async tasks running in parallel.
    """
    src = inspect.getsource(check_stalled_transfers)

    # Find the marker write by searching for the marker prefix in the source.
    # The source uses unescaped single quotes: details='Requeued by check_stalled_transfers:...'
    marker_idx = src.find("Requeued by check_stalled_transfers: transfers deleted")
    assert marker_idx != -1, (
        "Requeue marker text not found in check_stalled_transfers. "
        "Did the fix get reverted?"
    )

    # Look at a wider window around the marker
    window = src[max(0, marker_idx - 400): marker_idx + 600]
    assert "transfer_files_async.delay" in window, (
        "transfer_files_async.delay() not found near the requeue marker"
    )

    # The marker (.create) must come BEFORE .delay() in the source.
    marker_pos = window.find("ItemHistory.objects.create")
    delay_pos = window.find("transfer_files_async.delay")
    assert marker_pos != -1, "ItemHistory.objects.create not in window"
    assert delay_pos != -1, "transfer_files_async.delay not in window"
    assert marker_pos < delay_pos, (
        f"Cooldown marker (ItemHistory.objects.create) must be written BEFORE "
        f"transfer_files_async.delay() to prevent the requeue race. "
        f"Found marker at position {marker_pos} and delay at position {delay_pos} "
        f"in the Block B requeue block."
    )


def test_requeue_block_uses_exclude_status_preservation():
    """Lock-in for the Block B delete: `transfers.filter(status__in=['pending', 'failed']).delete()`
    must preserve 'completed' AND 'transferring' rows. This is the
    earlier preservation fix that prevents in-flight worker progress
    from being wiped every 5-min cooldown cycle."""
    src = inspect.getsource(check_stalled_transfers)
    assert "filter(status__in=['pending', 'failed']).delete()" in src, (
        "Block B's transfer delete is missing the status filter. "
        "Without it, in-flight (transferring) transfers are wiped every "
        "5-min cooldown cycle, resetting bytes_transferred to 0."
    )
    assert "transfers.exclude(status='completed').delete()" not in src, (
        "Block B is using transfers.exclude(status='completed').delete() "
        "which deletes transferring rows — preserves less than the new "
        "filter(status__in=['pending', 'failed']).delete() pattern."
    )
