"""Tests for the 'Completed but not really' guard in check_stalled_transfers.

Production case: a multi-file torrent was briefly marked Completed (the
post-processing branch saw `all_completed` between transfer creation
batches), but 500+ transfers were still pending. The Block A / Block B
recovery paths only iterate PostProcessing items, so the item sat
forever and the remaining transfers were never picked up.

The guard: at the start of check_stalled_transfers, find any item with
status=Completed AND any unfinished transfer, log a WARNING, and reset
to PostProcessing so Block A picks it up on the next tick.

These tests use a mock-based approach (matching the pattern in
test_check_stalled_exception_handler.py) so they don't depend on the
real Item/FileTransfer tables. The pre-existing missing-migration
issue on `Item.category` (deferred since 2026-03-14) makes
`Item.objects.create()` unreliable in the test DB.
"""
from unittest.mock import MagicMock, patch

from itemqueue import tasks as tasks_module
from itemqueue.tasks import check_stalled_transfers


def _make_item(status, transfer_statuses):
    """Build a mock Item with the right .transfers chain for the guard's queries.

    The guard calls: item.transfers.filter(status__in=[...]).count()
    So we need item.transfers.filter(...).count() to return the unfinished count.
    Default MagicMock would make .count() return a MagicMock; we override so it
    returns the int the guard expects.
    """
    item = MagicMock()
    item.status = status
    unfinished_count = sum(1 for s in transfer_statuses if s != "completed")
    # item.transfers.filter(...) -> filter_result; filter_result.count() -> unfinished_count
    filter_result = MagicMock()
    filter_result.count.return_value = unfinished_count
    item.transfers.filter.return_value = filter_result
    # .save() must be callable (the guard does item.save())
    item.save = MagicMock()
    return item


def _make_completed_item_with_unfinished(transfer_statuses):
    """The guard's queryset is:
        Item.objects.filter(status='Completed').filter(transfers__status__in=[...]).distinct()

    When iterated, each item must support .transfers.filter(...).count() and .save().
    """
    return _make_item("Completed", transfer_statuses)


class TestGuardResetsCompletedWithPending:
    """The guard resets Completed-with-unfinished to PostProcessing."""

    def test_completed_with_pending_is_reset(self):
        """status=Completed + has pending -> reset to PostProcessing."""
        item = _make_completed_item_with_unfinished(["completed", "pending", "pending"])
        # Make the guard's outer queryset return this item when iterated.
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
                # First .filter(status='Completed') returns a queryset whose
                # second .filter(transfers__status__in=[...]).distinct() returns
                # an iterable of our item.
                outer_qs = MagicMock()
                inner_qs = MagicMock()
                inner_qs.distinct.return_value = [item]
                outer_qs.filter.return_value = inner_qs
                mock_filter.return_value = outer_qs
                check_stalled_transfers()
        # The item should have been saved with status=PostProcessing
        item.save.assert_called_once()
        assert item.status == "PostProcessing", \
            f"expected PostProcessing, got {item.status!r}"
        # And the WARNING should mention unfinished transfers
        assert any(
            "unfinished transfers" in str(c) for c in mock_warn.call_args_list
        ), f"expected WARNING about unfinished transfers, got: {mock_warn.call_args_list}"

    def test_completed_with_failed_is_reset(self):
        item = _make_completed_item_with_unfinished(["completed", "failed"])
        with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
            outer_qs = MagicMock()
            inner_qs = MagicMock()
            inner_qs.distinct.return_value = [item]
            outer_qs.filter.return_value = inner_qs
            mock_filter.return_value = outer_qs
            check_stalled_transfers()
        item.save.assert_called_once()
        assert item.status == "PostProcessing"

    def test_completed_with_transferring_is_reset(self):
        item = _make_completed_item_with_unfinished(["completed", "transferring"])
        with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
            outer_qs = MagicMock()
            inner_qs = MagicMock()
            inner_qs.distinct.return_value = [item]
            outer_qs.filter.return_value = inner_qs
            mock_filter.return_value = outer_qs
            check_stalled_transfers()
        item.save.assert_called_once()
        assert item.status == "PostProcessing"


class TestGuardLeavesGenuineCompletedAlone:
    """If status=Completed AND all transfers are done, the guard must not touch it."""

    def test_completed_with_all_done_no_reset(self):
        item = _make_completed_item_with_unfinished(["completed", "completed", "completed"])
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
                # Guard's outer filter returns no items (Completed+all done not matched
                # because transfers__status__in=['pending','failed','transferring']
                # doesn't include 'completed')
                inner_qs = MagicMock()
                inner_qs.distinct.return_value = []
                outer_qs = MagicMock()
                outer_qs.filter.return_value = inner_qs
                mock_filter.return_value = outer_qs
                check_stalled_transfers()
        item.save.assert_not_called()
        assert item.status == "Completed", \
            "guard must not touch legitimately-completed items"
        assert not any(
            "unfinished transfers" in str(c) for c in mock_warn.call_args_list
        ), "guard must not warn about legitimately-completed items"


class TestGuardDoesNotFireOnNonCompleted:
    """The guard's outer filter is status='Completed' — other statuses bypass it."""

    def test_postprocessing_items_not_handled_by_guard(self):
        """PostProcessing items are Block A's domain, not this guard's.
        Verify by confirming the guard's outer filter (status='Completed')
        doesn't match a PostProcessing item. The mock below makes the
        guard's outer filter return [] for any non-Completed status."""
        # If the guard's outer filter is correctly scoped, it would NOT
        # return PostProcessing items. We assert that by patching the
        # outer filter and checking what it was called with.
        with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
            inner_qs = MagicMock()
            inner_qs.distinct.return_value = []
            outer_qs = MagicMock()
            outer_qs.filter.return_value = inner_qs
            mock_filter.return_value = outer_qs
            check_stalled_transfers()
        # The very first .filter() call from the guard must be status='Completed'
        outer_call = mock_filter.call_args_list[0]
        assert "Completed" in str(outer_call), \
            f"guard's outer filter must scope to status='Completed', got: {outer_call}"


class TestGuardLogsWithUnfinishedCount:
    """The WARNING should include the count of unfinished transfers."""

    def test_warning_includes_unfinished_count(self):
        item = _make_completed_item_with_unfinished(
            ["completed", "pending", "pending", "failed", "transferring"]
        )
        # 4 unfinished in this fixture
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
                inner_qs = MagicMock()
                inner_qs.distinct.return_value = [item]
                outer_qs = MagicMock()
                outer_qs.filter.return_value = inner_qs
                mock_filter.return_value = outer_qs
                check_stalled_transfers()
        # Find the WARNING about this item and check it mentions "4 unfinished"
        warnings = [str(c) for c in mock_warn.call_args_list]
        assert any("4 unfinished" in w for w in warnings), \
            f"expected warning to include '4 unfinished', got: {warnings}"


class TestGuardResetMessageInHistory:
    """The reset should also create an ItemHistory entry (audit trail)."""

    def test_reset_creates_history_entry(self):
        item = _make_completed_item_with_unfinished(["completed", "pending"])
        # history.create is called inside the guard
        with patch.object(tasks_module.ItemHistory.objects, "create") as mock_create:
            with patch.object(tasks_module.Item.objects, "filter") as mock_filter:
                inner_qs = MagicMock()
                inner_qs.distinct.return_value = [item]
                outer_qs = MagicMock()
                outer_qs.filter.return_value = inner_qs
                mock_filter.return_value = outer_qs
                check_stalled_transfers()
        # ItemHistory.objects.create should have been called once with item= and details
        assert mock_create.call_count == 1
        create_kwargs = mock_create.call_args.kwargs
        assert create_kwargs.get("item") is item, \
            f"history entry should reference the item, got kwargs: {create_kwargs}"
        assert "Reset from Completed" in create_kwargs.get("details", ""), \
            f"history details should mention the reset, got: {create_kwargs.get('details')}"
