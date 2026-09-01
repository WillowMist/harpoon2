"""COR-08: drain-on-idle dispatch gates in check_downloaders / _recover_one_item.

Phase 6-02 closes the worker-saturation cycle (AGENTS.md §"What's still
broken"): on 2026-09-01 four Celery workers each spent an hour transferring
the SAME item, gunicorn's workers starved on DB connections and died, and the
UI timed out on every page. The Phase 5-04 select_for_update lock only closes
the duplicate-ROW race; the 06-01 Redis semaphore closes the duplicate-WORK
race for tasks already queued. Neither stops the DISPATCHERS from queueing
more work into a saturated pool.

The fix is a drain-on-idle gate at the two dispatch sites:

    max_active = getattr(settings, 'MAX_CONCURRENT_TRANSFERS', 2)
    if _active_transfer_count() >= max_active:
        # skip this tick; the item waits for a worker slot to free
        continue

`_active_transfer_count()` counts transfer_files_async tasks currently active
on the celery pool via `celery_app.control.inspect().active()`, caches the
result for 5s, and fails OPEN (returns 0) when the inspect endpoint is
unreachable so dispatch still proceeds.

These tests mock `_active_transfer_count` (and the inspect endpoint for the
fail-open case); they never talk to a real broker.
"""
import logging

import pytest
from django.db import connection
from django.utils import timezone
from unittest.mock import Mock

from entities.models import Downloader
from itemqueue.models import Item, FileTransfer
from itemqueue.tasks import (
    _active_transfer_count,
    _active_count_cache,
    _recover_one_item,
    check_downloaders,
)


@pytest.fixture(autouse=True)
def _ensure_item_category_column(db):
    """Item.category has no migration (pre-existing schema drift, deferred
    since 2026-03-14), so the test DB lacks the column and real Item rows
    can't be created. Add it if missing so the behavioral test can use real
    DB rows. Mirrors tests/test_concurrent_transfer_lock.py."""
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


def _make_fake_downloader(completed_hash):
    """A Mock downloader whose client reports one completed hash."""
    fake_client = Mock()
    fake_client.get_completed.return_value = [{'hash': completed_hash}]
    fake_downloader = Mock()
    fake_downloader.client = fake_client
    fake_downloader.downloadertype = 'RTorrent'
    fake_downloader.name = 'fake'
    return fake_downloader


@pytest.mark.django_db
def test_check_downloaders_skips_when_at_threshold(db, monkeypatch):
    """active == MAX_CONCURRENT_TRANSFERS (2): check_downloaders must NOT
    dispatch postprocess_item for the completed hash — the pool is saturated
    and the item waits for a slot."""
    some_hash = 'C' * 40
    downloader_row = Downloader.objects.create(name='fake', downloadertype='RTorrent')
    Item.objects.create(hash=some_hash, name='A', status='Grabbed', downloader=downloader_row)

    monkeypatch.setattr(
        'itemqueue.tasks.Downloader.objects.all',
        lambda: [_make_fake_downloader(some_hash)],
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 2)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_downloaders()

    delay.assert_not_called()


@pytest.mark.django_db
def test_check_downloaders_proceeds_when_below_threshold(db, monkeypatch):
    """active (1) < MAX_CONCURRENT_TRANSFERS (2): check_downloaders MUST
    dispatch postprocess_item for the completed hash."""
    some_hash = 'E' * 40
    downloader_row = Downloader.objects.create(name='fake', downloadertype='RTorrent')
    Item.objects.create(hash=some_hash, name='A', status='Grabbed', downloader=downloader_row)

    monkeypatch.setattr(
        'itemqueue.tasks.Downloader.objects.all',
        lambda: [_make_fake_downloader(some_hash)],
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 1)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_downloaders()

    delay.assert_called_once_with(some_hash)


@pytest.mark.django_db
def test_recover_skips_when_at_threshold(db, monkeypatch):
    """active == MAX_CONCURRENT_TRANSFERS (2): _recover_one_item must defer
    recovery (return False) for a PostProcessing item with unfinished
    transfers, so check_stalled_transfers skips the transfer_files_async
    dispatch and the item is picked up on the next tick."""
    item = Item.objects.create(hash='F' * 40, name='A', status='PostProcessing')
    FileTransfer.objects.create(
        item=item,
        filename='f.bin',
        remote_path='/remote/f.bin',
        local_path='/local/f.bin',
        status='pending',
    )

    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 2)

    result = _recover_one_item(item, timezone.now())

    assert result is False, (
        "recovery must be deferred when the pool is at MAX_CONCURRENT_TRANSFERS"
    )
    item.refresh_from_db()
    assert item.status == 'PostProcessing', "item status must be untouched"


def test_inspect_unreachable_fails_open(monkeypatch):
    """Inspect endpoint down: _active_transfer_count() must return 0 (fail
    OPEN) and log a warning — the dispatcher should still proceed when the
    broker is unreachable, not deadlock the pipeline."""
    import harpoon2.celery as celery_module

    # Reset the 5s cache so this test exercises the real inspect path.
    _active_count_cache.update(value=0, ts=0)

    def boom(*args, **kwargs):
        raise ConnectionError("inspect unreachable")

    control = celery_module.app.control  # cached_property — same object the helper sees
    monkeypatch.setattr(control, 'inspect', boom)

    records = []
    capture = logging.Handler()
    capture.emit = lambda record: records.append(record)
    task_logger = logging.getLogger('itemqueue.tasks')
    task_logger.addHandler(capture)
    try:
        count = _active_transfer_count()
    finally:
        task_logger.removeHandler(capture)

    assert count == 0, "inspect failure must fail OPEN (return 0)"
    assert any(
        '[drain] inspect failed' in r.getMessage() for r in records
    ), "expected a drain inspect-failure warning in the log"