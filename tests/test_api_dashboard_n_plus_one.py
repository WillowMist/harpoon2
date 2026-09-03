"""Lock-in test: api_dashboard must not issue an O(N+1) query per FileTransfer row.

    The dashboard endpoint previously re-filtered FileTransfer by item hash inside
    the loop (one new DB query per distinct item), plus 4 COUNT queries per manager.
    With ~700 FileTransfer rows the request took >60s and gunicorn workers died on
    --timeout 60, then supervisord auto-restarted them — the cycle kept the UI
    broken. The fixed endpoint issues a bounded number of queries regardless of row
    count:

    1. one grouped aggregate for the per-manager status counts + global total
    2. one for CachedDownloaderStatus
    3. one for FileTransfer (item joined via select_related)
    4. one for recent_items (last 10 Completed/Failed, bounded with [:10] and
       select_related('manager', 'downloader') — no per-item N+1)
    """
import os

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harpoon2.settings")
django.setup()

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from entities.models import DownloadFolder, Manager
from itemqueue.models import FileTransfer, Item


@pytest.fixture(autouse=True)
def _ensure_item_category_column(db):
    """Item.category has no migration (pre-existing schema drift, deferred
    since 2026-03-14), so the test DB lacks the column and real Item rows
    can't be created. Add it if missing so the behavioral test can use real
    DB rows."""
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
def test_api_dashboard_query_count_is_bounded():
    """GET /api/dashboard/ with ~700 FileTransfer rows must issue <= 3 queries.

    Before the fix this request issued one query per distinct item hash (the
    O(N+1) scan) plus 4 COUNT queries per manager — enough to blow gunicorn's
    60s timeout. The response shape must be unchanged.
    """
    folder = DownloadFolder.objects.create(folder='/tmp/test')
    manager = Manager.objects.create(
        name='Test Manager', managertype='Sonarr', folder=folder,
        url='http://localhost:8989', apikey='test',
    )

    # One item with many transfers (the stalled-torrent shape: ~700 rows).
    item = Item.objects.create(
        hash='44C87CC876D3326C62A1FCD438D5DB12D3C4EFC1',
        name='item', status='PostProcessing', manager=manager,
        size=1000000, received=0,
    )
    for i in range(700):
        FileTransfer.objects.create(
            item=item,
            filename=f'file_{i}.mkv',
            file_size=1000,
            bytes_transferred=500,
            status='pending',
        )

    client = Client()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get('/api/dashboard/')
        assert response.status_code == 200, response.content

    # Count only the view's data-access queries. The Django session machinery
    # (session load/save + SAVEPOINT/RELEASE transaction control) adds fixed
    # framework overhead to every request and is not part of the dashboard's
    # data access.
    data_queries = [
        q for q in ctx.captured_queries
        if 'django_session' not in q['sql']
        and not q['sql'].lstrip().upper().startswith(('SAVEPOINT', 'RELEASE'))
    ]
    query_count = len(data_queries)

    assert query_count <= 4, (
        f"api_dashboard issued {query_count} data queries with 700 FileTransfer rows; "
        f"expected <= 4 (1 manager summary + 1 CachedDownloaderStatus + 1 FileTransfer "
        f"+ 1 recent_items; was O(N+1) before the fix).\n"
        + "\n".join(q['sql'][:200] for q in data_queries)
    )

    # Response shape must be unchanged (plus recent_items, the last 10
    # Completed/Failed items shown in the Recent Activity section).
    data = response.json()
    assert set(data.keys()) == {
        'manager_summary', 'grabbing_downloads', 'active_transfers',
        'total_speed_mbps', 'total_queued', 'recent_items',
    }
    assert data['recent_items'] == []
    assert data['manager_summary'][0]['grabbing'] == 0
    assert data['manager_summary'][0]['postprocessing'] == 1
    assert data['total_queued'] == 0
    # 700 pending transfers of 1000 bytes each, 500 transferred: one active
    # transfer at 50%.
    assert len(data['active_transfers']) == 1
    assert data['active_transfers'][0]['percent'] == 50
    assert data['active_transfers'][0]['file_count'] == 700


@pytest.mark.django_db
def test_api_dashboard_total_queued_includes_managerless_items():
    """total_queued must count Grabbed items with no manager (AirDC++-created).

    The manager summary query LEFT JOINs Manager -> Item, so manager-less
    items never appear in per-manager counts. The scalar subquery must add
    them back into total_queued — otherwise the dashboard undercounts and the
    quiet-alert logic misfires.
    """
    # One manager-less Grabbed item (the AirDC++ creation path sets no manager).
    Item.objects.create(
        hash='A' * 40, name='item', status='Grabbed', size=0, received=0,
    )

    client = Client()
    response = client.get('/api/dashboard/')
    assert response.status_code == 200, response.content
    data = response.json()

    assert data['total_queued'] == 1
    # No managers configured -> empty summary, but the count is still right.
    assert data['manager_summary'] == []
    # recent_items is the last 10 Completed/Failed items; the Grabbed item
    # created above does not appear (it is not finished).
    assert data['recent_items'] == []