"""Lock-in test for the Block B requeue preservation fix.

Block B's requeue path used to do `transfers.delete()` which wiped ALL
transfers (including completed ones). Combined with the cooldown fix,
this caused a 5-min cycle that wiped all progress every cycle. The
fix preserves both 'completed' (real progress) AND 'transferring'
(in-flight workers — killing them resets bytes_transferred to 0 and
oscillates the byte count by 500 MB-1 GB every cycle). Only
'pending' and 'failed' rows are deleted.
"""
import inspect

from itemqueue.tasks import check_stalled_transfers


def test_block_b_delete_preserves_completed():
    """Block B's requeue delete must preserve completed transfers so
    real progress survives the 5-min recovery cycle."""
    src = inspect.getsource(check_stalled_transfers)
    assert "transfers.filter(status__in=['pending', 'failed']).delete()" in src, (
        "Block B requeue should filter to ONLY pending and failed "
        "transfers — preserving both completed (real progress) and "
        "transferring (in-flight workers whose bytes_transferred would "
        "otherwise be reset to 0 every cycle)."
    )


def test_block_b_does_not_delete_transferring():
    """Lock-in: Block B must NOT delete 'transferring' rows. Killing
    in-flight workers loses bytes already copied and oscillates the
    byte count between cycles."""
    src = inspect.getsource(check_stalled_transfers)
    # Find the Block B requeue block
    requeue_idx = src.find("Requeued by check_stalled_transfers")
    assert requeue_idx != -1, "Block B requeue marker not found"
    # Window around the requeue (covers the delete line)
    window = src[max(0, requeue_idx - 1200): requeue_idx + 200]
    # The window must NOT contain `transfers.exclude(status='completed')`
    # (which would also exclude transferring)
    assert "transfers.exclude(status='completed').delete()" not in window, (
        "Block B is using transfers.exclude(status='completed').delete() "
        "— this ALSO deletes 'transferring' rows, losing in-flight "
        "worker progress. Use transfers.filter(status__in=['pending', 'failed']).delete() "
        "to preserve both completed and transferring."
    )
    assert "transfers.delete()" not in window, (
        "Block B is using bare transfers.delete() — wipes everything."
    )
