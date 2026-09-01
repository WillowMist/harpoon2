"""TEST-03: pin the 5-minute stall threshold at 300 seconds via freezegun.

The stall-detection threshold in check_stalled_transfers must be exactly
300 seconds (mirroring SFTP_GET_TIMEOUT_SECONDS). This is the first
freeze_time test in the repo: the Item and FileTransfer are created
OUTSIDE the frozen context (auto_now fields fire on .save(), which must
happen at real time), then time is advanced inside the freeze.

The Item's last_recovery_at is set to a future timestamp so the item stays
out of Phase B's recovery candidates — this isolates Phase A's stall
detection (the recovery pass would otherwise delete the failed transfer
as part of the requeue).
"""
import datetime

import pytest
from django.utils import timezone
from freezegun import freeze_time

from itemqueue.models import Item, FileTransfer
from itemqueue.tasks import (
    check_stalled_transfers,
    STALL_THRESHOLD_SECONDS,
    SFTP_GET_TIMEOUT_SECONDS,
)


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


def test_stall_threshold_constants_pinned():
    """The stall threshold and the SFTP watchdog timeout must both be 300s."""
    assert STALL_THRESHOLD_SECONDS == 300
    assert SFTP_GET_TIMEOUT_SECONDS == 300


@pytest.mark.django_db
def test_transfer_marked_failed_after_300_seconds(db):
    """A transferring transfer with no progress is still 'transferring' at
    299s and is marked 'failed' once the 300s threshold is crossed."""
    item = Item.objects.create(
        hash='D' * 40, name='D', status='PostProcessing',
        # Future last_recovery_at keeps the item out of Phase B's recovery
        # candidates so this test isolates Phase A's stall detection (the
        # recovery pass would otherwise delete the failed transfer).
        last_recovery_at=timezone.now() + datetime.timedelta(minutes=10),
    )
    transfer = FileTransfer.objects.create(
        item=item, filename='f', file_size=100, status='transferring',
        bytes_transferred=50, started=timezone.now(), modified=timezone.now(),
    )

    start = timezone.now()
    with freeze_time(start) as frozen:
        # 299s in: still inside the 300s threshold, transfer stays transferring.
        frozen.tick(delta=datetime.timedelta(seconds=299))
        check_stalled_transfers()
        transfer.refresh_from_db()
        assert transfer.status == 'transferring'

        # 300s in: threshold crossed, Phase A marks the transfer failed.
        frozen.tick(delta=datetime.timedelta(seconds=1))
        check_stalled_transfers()
        transfer.refresh_from_db()
        assert transfer.status == 'failed'