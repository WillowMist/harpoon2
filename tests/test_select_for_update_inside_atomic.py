"""Sanity: select_for_update requires an atomic block.

Per django/django docs/ref/models/querysets.md, select_for_update outside an
explicit transaction raises TransactionManagementError on supported databases
(PostgreSQL). SQLite silently no-ops the lock, so the outside-atomic test is
skipped there — its value is as the regression guard on PG: if a future
refactor moves the lock out of atomic(), this test catches it before the
production PG instance does.
"""
import pytest
from django.db import connection, transaction
from django.db.transaction import TransactionManagementError

from itemqueue.models import Item


@pytest.mark.django_db(transaction=True)
def test_select_for_update_outside_atomic_raises(db):
    """Outside atomic(), select_for_update must raise TransactionManagementError.

    This is the safety net for the lock-in test: if a future refactor
    accidentally moves the lock out of atomic(), this test catches it
    before the production PG instance does (SQLite silently no-ops the
    lock, which would mask the bug in local dev).
    """
    if connection.vendor == 'sqlite':
        pytest.skip(
            "SQLite silently no-ops select_for_update; the "
            "TransactionManagementError guard only fires on PostgreSQL"
        )
    with pytest.raises(TransactionManagementError):
        Item.objects.select_for_update(skip_locked=True).filter(hash='x').first()


@pytest.mark.django_db(transaction=True)
def test_select_for_update_inside_atomic_succeeds(db):
    """Inside atomic(), the lock is acquired cleanly. No exception."""
    with transaction.atomic():
        result = (
            Item.objects
            .select_for_update(skip_locked=True)
            .filter(hash='x')
            .first()
        )
    assert result is None  # no row, but the lock was acquired