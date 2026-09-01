"""Lock-in test for PIPE-04: Block B requeues, does not reimplement.

The duplicated extraction + post-process logic (process_zip_archives +
process_rar_archives + client.post_process + Blackhole temp->final move)
that used to live inline in check_stalled_transfers is removed. Those
operations are owned by transfer_files_async only; check_stalled_transfers
dispatches transfer_files_async.delay() and nothing else.
"""
import inspect

from itemqueue.tasks import check_stalled_transfers


def test_block_b_requeues_via_delay_only():
    """PIPE-04: the recovery path uses transfer_files_async.delay() and
    nothing else — no inline extraction / post-process / completion logic."""
    src = inspect.getsource(check_stalled_transfers)
    # The recovery path must still dispatch transfer_files_async.delay()
    # (this is the contract — see AGENTS.md "Transfer Pipeline Architecture").
    assert "transfer_files_async.delay" in src, (
        "check_stalled_transfers must still dispatch transfer_files_async.delay() "
        "for recovery — that is PIPE-04's 're-queue, don't reimplement'."
    )
    # ...and must NOT inline any of the three operations.
    assert "process_zip_archives(" not in src, (
        "check_stalled_transfers inlines process_zip_archives — PIPE-04 violation."
    )
    assert "process_rar_archives(" not in src, (
        "check_stalled_transfers inlines process_rar_archives — PIPE-04 violation."
    )
    assert ".post_process(" not in src, (
        "check_stalled_transfers inlines client.post_process() — PIPE-04 violation. "
        "Post-processing belongs in transfer_files_async, not check_stalled_transfers."
    )