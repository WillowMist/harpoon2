"""Pin case-insensitive Item hash lookups.

Background: Item.hash values may be stored in any case — some downloaders
(Bindery/qBittorrent) report uppercase hex, others (SABnzbd) use mixed
case. Postgres varchar is case-sensitive by default, so an exact
lookup misses if downloader and DB case differ.

The fix: every lookup of Item on a downloader-supplied hash uses
`hash__iexact`, which translates to `LOWER(hash) = LOWER(...)` on
Postgres. Same code path works for Bindery (uppercase vs lowercase
hex) and SABnzbd (mixed case same on both sides).
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
def test_postprocess_item_finds_mismatched_case_item():
    """postprocess_item called with UPPERCASE hash must find an Item stored as LOWERCASE."""
    from itemqueue.tasks import postprocess_item
    lower_hash = '820c8223f87d92b01ed0f42fe88b84ed374c5d86'
    upper_hash = lower_hash.upper()
    Item.objects.create(hash=lower_hash, status='Grabbed', name='case-test-1')
    # Patch only the network-touching parts of the rest of the function so
    # we can call it without crashing. We only care that the lookup succeeds.
    from unittest.mock import patch, MagicMock
    fake_dl = MagicMock()
    fake_dl.seedbox = MagicMock()
    with patch('itemqueue.tasks.Downloader.objects') as mgr_dl, \
         patch('itemqueue.tasks.postprocess_item.delay', create=True) as m_delay, \
         patch('itemqueue.tasks.Item.objects.get', side_effect=Item.objects.get):
        # If the lookup fails (lowercase only), this will raise DoesNotExist
        # before we hit the mocks below.
        try:
            postprocess_item(upper_hash)
        except Item.DoesNotExist:
            pytest.fail("postprocess_item raised DoesNotExist despite __iexact lookup")


@pytest.mark.django_db
def test_retry_postprocessing_finds_mismatched_case_item():
    """retry_postprocessing called with UPPERCASE hash must find Item stored as LOWERCASE."""
    from itemqueue.tasks import retry_postprocessing
    lower_hash = 'deadbeef00000000deadbeef00000000deadbeef'
    upper_hash = lower_hash.upper()
    Item.objects.create(hash=lower_hash, status='PostProcessing', name='case-test-2')
    try:
        retry_postprocessing(upper_hash)
    except Item.DoesNotExist:
        pytest.fail("retry_postprocessing raised DoesNotExist despite __iexact lookup")
