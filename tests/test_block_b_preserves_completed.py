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
    """Lock-in: Block B's requeue delete must use the explicit
    pending/failed filter (which excludes both completed and transferring).
    A regression to .exclude(status='completed') would also delete
    in-flight transfers, causing the byte-count oscillation."""
    src = inspect.getsource(check_stalled_transfers)
    # Find the Block B requeue block
    requeue_idx = src.find("Requeued by check_stalled_transfers")
    assert requeue_idx != -1, "Block B requeue marker not found"
    # Look at the actual delete call — it must filter to pending/failed,
    # not exclude from completed (which would also drop transferring).
    delete_idx = src.find(".delete()", requeue_idx - 200)
    assert delete_idx != -1, "delete() call not found near Block B requeue"
    # Look at the delete line (find the preceding "transfers" call)
    delete_line_start = src.rfind("transfers", requeue_idx - 200, delete_idx)
    delete_line_end = src.find("\n", delete_idx)
    delete_line = src[delete_line_start:delete_line_end].strip()
    assert "filter(status__in=['pending', 'failed'])" in delete_line, (
        f"Block B's delete line is wrong: {delete_line!r}. "
        f"Should filter to only pending and failed, preserving "
        f"both completed and transferring."
    )
    assert "exclude" not in delete_line, (
        f"Block B's delete uses .exclude() — would also delete "
        f"transferring rows, oscillating byte count. Use "
        f"filter(status__in=['pending', 'failed']) instead. Line: {delete_line!r}"
    )
