"""Lock-in test for the transfer_files_async band-aid removal.

The Phase 1 band-aid had an early-return that bailed out of
transfer_files_async if ANY pending/transferring FileTransfer existed
for the item. That prevented the function from creating transfers for
files it hadn't seen before — so a 120 GB torrent ended up with only
the first batch of transfers (25 GB / ~138 files) and could never
finish. The per-file dedup logic below the early-return already handles
duplicate prevention correctly.

These tests verify:
1. The early-return is GONE (the band-aid is removed)
2. The per-file dedup leaves in-flight (pending/transferring) transfers alone
"""
import inspect

from itemqueue import tasks as tasks_module
from itemqueue.tasks import transfer_files_async


def test_no_early_return_on_pending_or_transferring():
    """The band-aid's early-return must be removed.

    The old code had:
        if existing_transfers.exists():
            ...return

    This test locks in that the early-return no longer exists — if it
    does come back, this test fails.
    """
    src = inspect.getsource(transfer_files_async)
    # The exact early-return string that was removed
    forbidden = "if existing_transfers.exists():\n            logger.info(f\"Item {item.name} already has {existing_transfers.count()} pending/transferring records. Skipping duplicate transfer creation.\""
    assert forbidden not in src, (
        "transfer_files_async still has the band-aid early-return — this "
        "bails out before creating missing transfers for files the prior "
        "run never saw, leaving a torrent stuck at the first batch of transfers."
    )


def test_per_file_dedup_handles_in_flight_transfers():
    """The per-file dedup branch must leave in-flight transfers alone.

    The old code lumped 'pending'/'transferring' into the 'else: delete' branch,
    which would lose bytes already copied. The fix splits this into a separate
    elif that logs and continues without touching the transfer.
    """
    src = inspect.getsource(transfer_files_async)
    # The in-flight branch must exist (lock-in for the elif).
    assert "existing_transfer.status in ('pending', 'transferring')" in src, (
        "per-file dedup must have a separate branch for in-flight transfers "
        "(pending/transferring) — leaving them alone prevents data loss when "
        "transfer_files_async runs while a previous run is mid-flight."
    )
    # The in-flight branch must NOT delete the transfer (the bug we're fixing)
    # Look for the in-flight branch body
    in_flight_idx = src.find("existing_transfer.status in ('pending', 'transferring')")
    branch_body = src[in_flight_idx:in_flight_idx + 800]
    # The branch should `continue`, not delete
    assert "continue" in branch_body, "in-flight branch must continue (skip), not delete"
    # The branch should NOT call existing_transfer.delete()
    assert "existing_transfer.delete()" not in branch_body, (
        "in-flight branch must NOT call existing_transfer.delete() — "
        "deleting an in-progress transfer loses bytes already copied"
    )


def test_no_dead_references_to_existing_transfers():
    """The local `existing_transfers` variable in the early-return must
    no longer exist — if a future change reintroduces a similar guard
    using the same variable name, this test catches it."""
    src = inspect.getsource(transfer_files_async)
    # The early-return used `existing_transfers = FileTransfer.objects.filter(...)`.
    # A future guard might reuse this name and reintroduce the bug.
    # Look for `existing_transfers.exists()` which is the specific bug pattern.
    assert "existing_transfers.exists()" not in src, (
        "transfer_files_async uses 'existing_transfers.exists()' — this is "
        "the pattern of the removed band-aid. Reintroducing it would re-break "
        "the band-aid fix."
    )
