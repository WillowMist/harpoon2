"""PIPE-01 lock-in + behavioral test for the retry_postprocessing attempt cap.

The 4th retry_postprocessing invocation must be a no-op: no manager.post_process
call, item marked Failed. The cap is enforced two ways:
  (a) the `attempt` argument carried by the deterministic task_id, and
  (b) the persisted Item.attempt_count column (survives worker restarts).

The behavioral test creates the Item with attempt_count=3 (cap reached) and
calls retry_postprocessing with the default attempt=1 — the persisted-column
check must block the call. The autouse fixture mirrors
tests/test_last_recovery_at_cooldown.py for the pre-existing Item.category
schema drift.
"""
import inspect
from unittest.mock import patch

import pytest
from django.db import connection

from itemqueue.models import Item
from itemqueue.tasks import retry_postprocessing, RETRY_CAP_ATTEMPTS


@pytest.fixture(autouse=True)
def _ensure_item_category_column(db):
    """Item.category has no migration (pre-existing schema drift, deferred
    since 2026-03-14), so the test DB lacks the column and real Item rows
    can't be created. Add it if missing so the behavioral test can use real
    DB rows. Mirrors tests/test_last_recovery_at_cooldown.py."""
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


def test_attempt_cap_constant_is_3():
    """PIPE-01 hard cap = 3, pinned as a module-level constant."""
    assert RETRY_CAP_ATTEMPTS == 3


def test_attempt_cap_enforced_in_source():
    """PIPE-01: the task body must check `attempt > RETRY_CAP_ATTEMPTS`
    (or equivalent) so the 4th invocation is a no-op."""
    src = inspect.getsource(retry_postprocessing)
    assert (
        "attempt > RETRY_CAP_ATTEMPTS" in src
        or "next_attempt > RETRY_CAP_ATTEMPTS" in src
        or "attempt > 3" in src
        or "attempt >= 4" in src
    ), (
        "retry_postprocessing must hard-cap attempts at 3 (PIPE-01). "
        "Look for `attempt > RETRY_CAP_ATTEMPTS` or `attempt > 3` in the source."
    )


@pytest.mark.django_db
def test_fourth_invocation_is_noop(db):
    """With Item.attempt_count = 3 (cap reached), the task must NOT call
    manager.post_process and must mark the item Failed."""
    item = Item.objects.create(
        hash='B' * 40, name='B', status='PostProcessing', attempt_count=3,
    )
    # Mock manager.post_process; expect zero calls
    with patch('entities.managers.Bindery.post_process') as mock_pp:
        retry_postprocessing('B' * 40)
    mock_pp.assert_not_called()
    item.refresh_from_db()
    assert item.status == 'Failed'


@pytest.mark.django_db
def test_attempt_argument_over_cap_is_noop(db):
    """With attempt=4 (argument path), the task must NOT call
    manager.post_process and must mark the item Failed."""
    item = Item.objects.create(
        hash='E' * 40, name='E', status='PostProcessing',
    )
    with patch('entities.managers.Bindery.post_process') as mock_pp:
        retry_postprocessing('E' * 40, attempt=4)
    mock_pp.assert_not_called()
    item.refresh_from_db()
    assert item.status == 'Failed'