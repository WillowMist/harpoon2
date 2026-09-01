"""06-03: AirDC++ completion-check beat task.

AirDC++ doesn't emit a download-complete event harpoon can see — its events
API is capped at 100 and the Mylar3 log feed only emits "Attempting to
download", not completion. Items assigned to an AirDC++ downloader therefore
sit in 'Grabbed' forever. The fix: when an Item is created with AirDC++ as
the downloader, store the expected file/folder name + a check time. A new
beat task (check_airdcpp_completions, every 5 min) SFTP-walks the AirDC++
share, finds the file, and dispatches postprocess_item.

These tests mock the SFTP connection (itemqueue.tasks._sftp_connect_with_retry)
and the drain gate; they never touch a real seedbox or broker.
"""
import pytest
from django.db import connection
from django.utils import timezone
from datetime import timedelta
from unittest.mock import Mock

from entities.models import Downloader, Seedbox
from itemqueue.models import Item, ItemHistory
from itemqueue.tasks import check_airdcpp_completions


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


class _FakeSFTP:
    """Minimal paramiko-SFTPClient stand-in: listdir_attr returns entries for
    a path (either a single list for every path, or a path->entries mapping),
    close() records the call."""

    def __init__(self, entries):
        # Allow path-aware trees: {'/Downloads': [...], '/Downloads/sub': [...]}
        self._mapping = isinstance(entries, dict)
        self.entries = entries
        self.closed = False

    def listdir_attr(self, path):
        if self._mapping:
            return self.entries.get(path, [])
        return self.entries

    def close(self):
        self.closed = True


class _FakeSSH:
    """Minimal paramiko-SSHClient stand-in: open_sftp returns a fake SFTP."""

    def __init__(self, sftp):
        self._sftp = sftp
        self.closed = False

    def open_sftp(self):
        return self._sftp

    def close(self):
        self.closed = True


def _dir_entry(name):
    e = Mock()
    e.filename = name
    e.longname = 'drwxr-xr-x 1 user group 0 Jan 1 00:00 ' + name
    return e


def _file_entry(name):
    e = Mock()
    e.filename = name
    e.longname = '-rw-r--r-- 1 user group 100 Jan 1 00:00 ' + name
    return e


def _make_airdcpp_item(hash, expected_path, check_count=0):
    seedbox = Seedbox.objects.create(
        name='sb', host='seedbox.example', port=22, username='u',
        auth_type='password', password='p', base_download_folder='/Downloads',
    )
    downloader = Downloader.objects.create(
        name='airdcpp', downloadertype='AirDC++', seedbox=seedbox,
    )
    item = Item.objects.create(
        hash=hash, name='x', status='Grabbed', downloader=downloader,
        next_check_at=timezone.now() - timedelta(seconds=1),
        airdcpp_expected_path=expected_path,
        airdcpp_check_count=check_count,
    )
    return item


@pytest.mark.django_db
def test_found_dispatches_postprocess(db, monkeypatch):
    """SFTP walk finds the expected file -> postprocess_item.delay is called,
    the timer is cleared, and the check count resets to 0."""
    item = _make_airdcpp_item('A' * 40, 'somefile.cbz')
    sftp = _FakeSFTP([_file_entry('somefile.cbz')])
    monkeypatch.setattr(
        'itemqueue.tasks._sftp_connect_with_retry', lambda seedbox: _FakeSSH(sftp)
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 0)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_airdcpp_completions()

    delay.assert_called_once_with(item.hash)
    item.refresh_from_db()
    assert item.next_check_at is None
    assert item.airdcpp_check_count == 0


@pytest.mark.django_db
def test_found_in_subdirectory(db, monkeypatch):
    """The walk recurses into directories and finds the target nested."""
    item = _make_airdcpp_item('D' * 40, 'nested.cbz')
    sftp = _FakeSFTP({
        '/Downloads': [_dir_entry('sub'), _file_entry('other.cbz')],
        '/Downloads/sub': [_file_entry('nested.cbz')],
    })
    monkeypatch.setattr(
        'itemqueue.tasks._sftp_connect_with_retry', lambda seedbox: _FakeSSH(sftp)
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 0)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_airdcpp_completions()

    delay.assert_called_once_with(item.hash)
    item.refresh_from_db()
    assert item.next_check_at is None


@pytest.mark.django_db
def test_not_found_pushes_timer_and_increments(db, monkeypatch):
    """Walk doesn't find the file -> next_check_at is pushed out and
    airdcpp_check_count increments; no dispatch."""
    item = _make_airdcpp_item('B' * 40, 'missing.cbz')
    sftp = _FakeSFTP([_file_entry('other.cbz')])
    monkeypatch.setattr(
        'itemqueue.tasks._sftp_connect_with_retry', lambda seedbox: _FakeSSH(sftp)
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 0)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_airdcpp_completions()

    delay.assert_not_called()
    item.refresh_from_db()
    assert item.airdcpp_check_count == 1
    assert item.next_check_at is not None
    assert item.next_check_at > timezone.now()


@pytest.mark.django_db
def test_max_checks_marks_failed(db, monkeypatch):
    """After AIRDCPP_MAX_CHECKS failures the item is marked Failed and an
    ItemHistory row is created."""
    monkeypatch.setenv('AIRDCPP_MAX_CHECKS', '3')
    item = _make_airdcpp_item('C' * 40, 'never.cbz', check_count=2)
    sftp = _FakeSFTP([_file_entry('other.cbz')])
    monkeypatch.setattr(
        'itemqueue.tasks._sftp_connect_with_retry', lambda seedbox: _FakeSSH(sftp)
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 0)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_airdcpp_completions()

    delay.assert_not_called()
    item.refresh_from_db()
    assert item.status == 'Failed'
    assert item.airdcpp_check_count == 3
    assert ItemHistory.objects.filter(item=item).exists()


@pytest.mark.django_db
def test_drain_gate_defers_dispatch(db, monkeypatch):
    """Pool at MAX_CONCURRENT_TRANSFERS: the found file is NOT dispatched;
    the timer is pushed out so the next tick retries."""
    item = _make_airdcpp_item('E' * 40, 'somefile.cbz')
    sftp = _FakeSFTP([_file_entry('somefile.cbz')])
    monkeypatch.setattr(
        'itemqueue.tasks._sftp_connect_with_retry', lambda seedbox: _FakeSSH(sftp)
    )
    monkeypatch.setattr('itemqueue.tasks._active_transfer_count', lambda: 2)
    delay = Mock()
    monkeypatch.setattr('itemqueue.tasks.postprocess_item.delay', delay)

    check_airdcpp_completions()

    delay.assert_not_called()
    item.refresh_from_db()
    assert item.next_check_at is not None
    assert item.next_check_at > timezone.now()