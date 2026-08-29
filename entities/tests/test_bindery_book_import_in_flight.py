"""Tests for Bindery._book_import_in_flight (COR-02): rows with null/0
bookId must not match; calls with a falsy book_id must short-circuit
without making any HTTP call.

Uses `responses` (already in requirements-dev.txt) to mock Bindery HTTP.
"""
import pytest
import responses

from entities.managers import Bindery


@pytest.fixture
def bindery():
    """Minimal Bindery stub: __init__ takes a manager; bypass by direct init."""
    bindery = Bindery.__new__(Bindery)
    bindery.apiurl = 'http://test-bindery:8787/api/v1'
    bindery.headers = {'X-Api-Key': 'test-key', 'Accept': 'application/json'}
    return bindery


@responses.activate
def test_skips_null_bookid_in_row(bindery):
    """A row with bookId=null must not match a non-null book_id.

    Regression test for COR-02: the old `int(r.get('bookId') or 0) ==
    int(book_id or 0)` coercion made both null rows and book_id=0 coerce
    to 0, so a null row would spuriously match book_id=0. With the new
    truthy-guard `(r.get('bookId') or 0) and int(...) == int(book_id)`,
    null/0/missing bookId rows are skipped.
    """
    responses.add(
        responses.GET,
        'http://test-bindery:8787/api/v1/queue',
        json={'items': [{'id': 1, 'bookId': None, 'status': 'importing'}]},
        status=200,
    )
    assert bindery._book_import_in_flight(42) is False


@responses.activate
def test_skips_zero_bookid_in_row(bindery):
    """A row with bookId=0 must not match a non-zero book_id."""
    responses.add(
        responses.GET,
        'http://test-bindery:8787/api/v1/queue',
        json={'items': [{'id': 1, 'bookId': 0, 'status': 'importing'}]},
        status=200,
    )
    assert bindery._book_import_in_flight(42) is False


def test_falsy_book_id_short_circuits_without_http(bindery):
    """A falsy book_id must return False without making any HTTP call.

    Proves the new `if not book_id: return False` short-circuit at the top
    of the predicate body. Before the fix, all three of these calls would
    hit Bindery's /api/v1/queue endpoint — wasted HTTP round-trips when the
    caller already knows the bookId is missing.
    """
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        assert bindery._book_import_in_flight(None) is False
        assert bindery._book_import_in_flight(0) is False
        assert bindery._book_import_in_flight('') is False
        # No HTTP calls should have been registered.
        assert len(rsps.calls) == 0


@responses.activate
def test_matches_truthy_bookid_with_active_status(bindery):
    """A row with bookId=42 and status=importing must match.

    Locks in the documented happy path: the predicate's only True case is
    when the queue has a row whose bookId equals the caller's book_id AND
    whose status is in the active set.
    """
    responses.add(
        responses.GET,
        'http://test-bindery:8787/api/v1/queue',
        json={'items': [{'id': 1, 'bookId': 42, 'status': 'importing'}]},
        status=200,
    )
    assert bindery._book_import_in_flight(42) is True
