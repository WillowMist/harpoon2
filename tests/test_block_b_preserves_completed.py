"""Lock-in test for the Block B requeue preservation fix.

Block B's requeue path used to do `transfers.delete()` which wiped ALL
transfers (including completed ones). Combined with the cooldown fix,
this caused a 5-min cycle that wiped all progress every cycle. The
fix changes the delete to `transfers.exclude(status='completed').delete()`
so completed transfers are preserved and only stale failed/pending/
transferring rows are removed.

This test verifies the fix line is present.
"""
import inspect

from itemqueue.tasks import check_stalled_transfers


def test_block_b_delete_preserves_completed():
    """Block B's requeue delete must use .exclude(status='completed') so
    completed work survives the 5-min recovery cycle."""
    src = inspect.getsource(check_stalled_transfers)
    assert "transfers.exclude(status='completed').delete()" in src, (
        "Block B requeue is using unconditional delete() — this wipes "
        "completed transfers every 5-min cooldown cycle, oscillating the "
        "byte count back to 0 each time. Use transfers.exclude(status='completed').delete() "
        "to preserve completed work."
    )
