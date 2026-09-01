"""PIPE-02 + COR-06: concurrent select_for_update(skip_locked=True) lock test.

The duplicate-FileTransfer race (AGENTS.md §"What's still broken" #1) happens
when two Celery workers run transfer_files_async on the same item.hash at the
same time. The fix is a row-level lock at the function entry:

    with transaction.atomic():
        item = (
            Item.objects
            .select_for_update(skip_locked=True)
            .filter(hash=item_hash)
            .first()
        )

The second worker's skip_locked=True query observes the row is locked and
returns None — it exits without writing any FileTransfer rows.

This is the FIRST TransactionTestCase (via @pytest.mark.django_db(transaction=True))
in the repo. TestCase (pytest-django's default) wraps each test in its own
transaction that masks the lock behavior; transaction=True commits each
statement so the lock is visible across threads.

SQLite note: select_for_update is a silent no-op on SQLite (local dev). Both
threads see the row, so the assertion is backend-aware: on PostgreSQL exactly
one thread wins; on SQLite both see the row. The test's real value is on PG
(production) — do not disable it because it "doesn't test concurrency locally".
"""
import threading

import pytest
from django.db import connection, transaction

from itemqueue.models import Item


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


@pytest.mark.django_db(transaction=True)
def test_concurrent_select_for_update_exactly_one_winner(db):
    """Two concurrent select_for_update(skip_locked=True) queries on the same
    Item hash result in exactly one thread acquiring the lock; the other sees
    None (skip_locked=True on contention).

    Two barriers make the race deterministic without time.sleep:
      - `entered` releases both threads into the locked section simultaneously.
      - `release` is waited on INSIDE the atomic block, so the lock winner
        holds the row lock until the loser has completed its query. The loser
        therefore MUST observe the locked row and skip it.
    """
    item = Item.objects.create(hash='A' * 40, name='A', status='PostProcessing')

    results = []
    entered = threading.Barrier(2)
    release = threading.Barrier(2)

    def run():
        entered.wait()
        try:
            with transaction.atomic():
                locked = (
                    Item.objects
                    .select_for_update(skip_locked=True)
                    .filter(hash=item.hash)
                    .first()
                )
                results.append(locked)
                # Hold the lock until the other thread has attempted its query.
                # Timeout guards against a hang if the other thread errors out.
                release.wait(timeout=10)
        finally:
            # If the other thread errored, don't leave it stuck at the barrier.
            try:
                release.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    seen = [r is not None for r in results]
    if connection.vendor == 'postgresql':
        # Exactly one worker acquires the lock; the other skips and sees None.
        assert sum(seen) == 1, (
            f"Expected exactly one worker to acquire the lock; got {seen}"
        )
    else:
        # SQLite silently no-ops select_for_update — both threads see the row.
        assert sum(seen) == 2, (
            f"SQLite no-ops select_for_update; expected both threads to see "
            f"the row; got {seen}"
        )