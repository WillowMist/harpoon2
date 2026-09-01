"""Lock-in test for the unified recovery state machine (Phase 5 PIPE-02 + PIPE-04).

Per AGENTS.md §"What's still broken": "check_stalled_transfers has two
interleaved recovery blocks (Block A vs Block B) with subtle ordering
dependencies and race windows." Phase 5 unifies them into one ordered
state machine driven by Item.last_recovery_at. This test pins that the
per-item recovery decision lives in a single `_recover_one_item(item, now)`
helper referenced from check_stalled_transfers.
"""
import inspect

from itemqueue import tasks as tasks_module
from itemqueue.tasks import check_stalled_transfers


def test_unified_state_machine_has_recover_one_item_helper():
    """The new check_stalled_transfers must call a `_recover_one_item(item, now)`
    helper for each PostProcessing candidate, replacing the inline Block A and
    Block B branches. Both the 'mark stalled transfer as failed' (Block A) and
    the 'requeue PostProcessing item' (Block B) decisions now live in one place."""
    src = inspect.getsource(check_stalled_transfers)
    assert "_recover_one_item" in src, (
        "check_stalled_transfers must call a _recover_one_item(item, now) "
        "helper for the unified recovery state machine (Phase 5 PIPE-02 + PIPE-04)."
    )


def test_recover_one_item_is_module_level():
    """_recover_one_item must be defined at module top level (not nested inside
    check_stalled_transfers) and take the (item, now) signature."""
    module_src = inspect.getsource(tasks_module)
    assert "def _recover_one_item(item, now):" in module_src, (
        "_recover_one_item must be a module-level function taking (item, now)."
    )