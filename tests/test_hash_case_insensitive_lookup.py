"""Pin the lowercase-hash lookup fix for case-mismatched Item hashes.

Background: some Item rows in Postgres are stored with lowercase hashes
(from one ingestion path), while downloaders report uppercase hashes.
`Item.objects.get(hash=item_hash)` then returns DoesNotExist because
Postgres varchar is case-sensitive. Lowercasing the lookup hash at every
dispatch entry point makes the dispatcher robust to either casing.
"""
import pytest
from django.db import connection
from itemqueue.models import Item


@pytest.fixture(autouse=True)
def _ensure_item_category_column(db):
    """Item.category has no migration (pre-existing schema drift, deferred
    since 2026-03-14). Same fixture pattern used in
    tests/test_concurrent_transfer_lock.py and tests/test_last_recovery_at_cooldown.py."""
    with connection.cursor() as cursor:
        if connection.vendor == 'sqlite':
            cursor.execute(
                "SELECT COUNT(*) FROM pragma_table_info('itemqueue_item') WHERE name='category'"
            )
            exists = cursor.fetchone()[0]
            if not exists:
                cursor.execute("ALTER TABLE itemqueue_item ADD COLUMN category VARCHAR(50) DEFAULT ''")
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name='itemqueue_item' AND column_name='category'"
            )
            exists = cursor.fetchone()[0]
            if not exists:
                cursor.execute("ALTER TABLE itemqueue_item ADD COLUMN category VARCHAR(50) DEFAULT ''")
    yield


@pytest.mark.django_db
def test_postprocess_item_lowercases_hash_for_lookup():
    """Downloader reports uppercase; Item is stored lowercase. Lookup must resolve."""
    from unittest.mock import patch
    lower_hash = '820c8223f87d92b01ed0f42fe88b84ed374c5d86'
    upper_hash = lower_hash.upper()
    Item.objects.create(hash=lower_hash, status='Grabbed', name='hash-case-test-1')
    # Capture how Item.objects.get is called inside postprocess_item
    real_get = Item.objects.get
    captured_kwargs = {}
    def capturing_get(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_get(*args, **kwargs)
    with patch('itemqueue.tasks.Item.objects.get', side_effect=capturing_get):
        # postprocess_item will return early after the lookup (no downloader
        # configured); we only care about the lookup itself.
        try:
            from itemqueue.tasks import postprocess_item
            postprocess_item(upper_hash)
        except Exception:
            pass  # postprocess_item may raise on later steps; lookup itself succeeded
    assert captured_kwargs.get('hash') == lower_hash, (
        f"Expected lowercased hash {lower_hash}, got {captured_kwargs.get('hash')}"
    )


@pytest.mark.django_db
def test_retry_postprocessing_lowercases_hash():
    from unittest.mock import patch
    lower_hash = 'deadbeef00000000deadbeef00000000deadbeef'
    upper_hash = lower_hash.upper()
    Item.objects.create(hash=lower_hash, status='PostProcessing', name='hash-case-test-3')
    real_get = Item.objects.get
    captured_kwargs = {}
    def capturing_get(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_get(*args, **kwargs)
    with patch('itemqueue.tasks.Item.objects.get', side_effect=capturing_get):
        from itemqueue.tasks import retry_postprocessing
        try:
            retry_postprocessing(upper_hash)
        except Exception:
            pass
    assert captured_kwargs.get('hash') == lower_hash
