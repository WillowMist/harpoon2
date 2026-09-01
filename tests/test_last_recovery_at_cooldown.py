"""PIPE-03 lock-in + behavioral test for the Item.last_recovery_at cooldown.

The cooldown gate in check_stalled_transfers must read Item.last_recovery_at
(the single source of truth), NOT scan ItemHistory for the old
'Requeued by check_stalled_transfers' prefix. The window is 60 seconds.

The behavioral test creates the Item and FileTransfer OUTSIDE the frozen
context (auto_now fields fire on .save(), which must happen at real time),
then advances time inside the freeze.
"""
import datetime
import inspect
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone
from freezegun import freeze_time

from itemqueue.models import Item, FileTransfer
from itemqueue.tasks import check_stalled_transfers


@pytest.fixture(autouse=True)
def _ensure_item_category_column(db):
    """Item.category has no migration (pre-existing schema drift, deferred
    since 2026-03-14), so the test DB lacks the column and real Item rows
    can't be created. Add it if missing so the behavioral test can use real
    DB rows (per the plan's freeze_time.tick requirement)."""
    from django.db import connection
    with connection.cursor() as cursor:
        if connection.vendor == 'sqlite':
            cursor.execute(
                "SELECT COUNT(*) FROM pragma_table_info('itemqueue_item') WHERE name='category'"
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name='itemqueue_item' AND column_name='category'"
            )
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE itemqueue_item ADD COLUMN category varchar(100) NOT NULL DEFAULT ''"
            )
    yield


def test_cooldown_uses_last_recovery_at_column_not_itemhistory():
    """PIPE-03: the cooldown gate must read Item.last_recovery_at, not
    scan ItemHistory. The ItemHistory-based prefix scan is the old contract."""
    src = inspect.getsource(check_stalled_transfers)
    assert "last_recovery_at" in src, (
        "check_stalled_transfers must read Item.last_recovery_at for the cooldown"
    )
    # The ItemHistory-based prefix scan must be removed from the active path.
    assert "details__startswith='Requeued by check_stalled_transfers'" not in src, (
        "Old ItemHistory cooldown scan is still in check_stalled_transfers. "
        "Per PIPE-03, the cooldown moves to Item.last_recovery_at; the ItemHistory "
        "rows become pure audit."
    )


@pytest.mark.skipif(
    not getattr(settings, 'PIPELINE_HARDENING_ENABLED', True),
    reason="PIPELINE_HARDENING_ENABLED is off; legacy path active",
)
@pytest.mark.django_db
def test_60s_cooldown_blocks_second_check(db):
    """PIPE-03 behavioral: 30s after recovery, the next check skips the item;
    at 61s the cooldown has elapsed and the next check proceeds."""
    item = Item.objects.create(
        hash='C' * 40, name='C', status='PostProcessing',
        last_recovery_at=timezone.now(),
    )
    FileTransfer.objects.create(
        item=item, filename='f', file_size=100, status='pending',
    )

    start = timezone.now()
    with freeze_time(start) as frozen:
        # 30s in: still inside the 60s cooldown, the check skips the item.
        frozen.tick(delta=datetime.timedelta(seconds=30))
        with patch('itemqueue.tasks.transfer_files_async.delay') as mock_delay:
            check_stalled_transfers()
        mock_delay.assert_not_called()

        # 61s in: cooldown elapsed, the check recovers the item and dispatches.
        frozen.tick(delta=datetime.timedelta(seconds=31))
        with patch('itemqueue.tasks.transfer_files_async.delay') as mock_delay2:
            check_stalled_transfers()
        mock_delay2.assert_called()