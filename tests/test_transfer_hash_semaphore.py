"""COR-07: per-hash Redis semaphore in transfer_files_async.

Phase 6-01 closes the duplicate-work race (AGENTS.md §"What's still broken"):
on 2026-09-01 four Celery workers each spent an hour transferring the SAME
item because the Phase 5-04 select_for_update(skip_locked=True) lock only
closes the duplicate-ROW race, not the duplicate-WORK race. The lock is
released ~10ms after acquire; four queued tasks for one hash then each
proceed to SFTP sequentially.

The fix is a per-hash Redis INCR semaphore immediately after the row lock:

    count = redis_client.incr(f"transfer_lock:{item_hash}")
    if count > 1:
        redis_client.decr(...)   # second arrival exits cleanly
        return
    redis_client.expire(f"transfer_lock:{item_hash}", 300)  # 5-min TTL

The work body runs in try/finally with DECR in finally, so the slot is
released on success, exception, or early return. Redis-down fails OPEN
(log warning, proceed) — the DB row lock remains defense-in-depth.

These tests mock the redis client; they never touch a real Redis.
"""
import logging

import pytest
import redis
from django.db import connection
from unittest.mock import Mock

from itemqueue.models import Item
from itemqueue.tasks import transfer_files_async


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


@pytest.mark.django_db
def test_second_arrival_exits_cleanly(db, monkeypatch):
    """When INCR returns 2, the second arrival DECRs and exits before doing
    any work — no downloader client access, no expire, no DB writes beyond
    the initial lookup."""
    item = Item.objects.create(hash='a' * 40, name='a', status='Grabbed')

    fake_redis = Mock()
    fake_redis.incr.return_value = 2
    monkeypatch.setattr('itemqueue.tasks._get_redis_client', lambda: fake_redis)

    result = transfer_files_async(item.hash)

    assert result is None
    fake_redis.incr.assert_called_once_with(f"transfer_lock:{item.hash}")
    # Second arrival releases the slot it never owned and does NOT set a TTL.
    fake_redis.decr.assert_called_once_with(f"transfer_lock:{item.hash}")
    fake_redis.expire.assert_not_called()


@pytest.mark.django_db
def test_redis_down_proceeds_with_warning(db, monkeypatch):
    """Redis unreachable = fail OPEN: the task logs a warning and proceeds
    without the semaphore (the DB row lock from Phase 5-04 is the backstop).
    The item has no downloader, so after the semaphore is bypassed the task
    reaches the no-downloader guard and exits cleanly — proving it got past
    the semaphore instead of crashing on it."""
    item = Item.objects.create(hash='b' * 40, name='b', status='Grabbed')

    fake_redis = Mock()
    fake_redis.incr.side_effect = redis.ConnectionError("redis is down")
    monkeypatch.setattr('itemqueue.tasks._get_redis_client', lambda: fake_redis)

    # The itemqueue logger has propagate=False, so caplog (root handler) never
    # sees its records. Attach a capture handler directly to the module logger.
    records = []
    capture = logging.Handler()
    capture.emit = lambda record: records.append(record)
    task_logger = logging.getLogger('itemqueue.tasks')
    task_logger.addHandler(capture)
    try:
        result = transfer_files_async(item.hash)
    finally:
        task_logger.removeHandler(capture)

    assert result is None
    fake_redis.incr.assert_called_once_with(f"transfer_lock:{item.hash}")
    # Never acquired, never released, no TTL.
    fake_redis.decr.assert_not_called()
    fake_redis.expire.assert_not_called()
    assert any(
        'Redis unavailable' in r.getMessage() for r in records
    ), "expected a Redis-unavailable warning in the log"