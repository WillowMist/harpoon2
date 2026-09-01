"""PIPE-07: tenacity retry behavior on Manager API calls.

Covers Arr.test() (via the _api_get wrapper) and Bindery.test():
  - transient ConnectionErrors are absorbed; the call eventually succeeds
  - after the retry budget is exhausted, the caller sees the original
    ConnectionError via the (False, msg) contract (reraise=True — NOT
    tenacity.RetryError)

Note on attempt counts: stop_after_attempt(5) caps the budget at 5 TOTAL
attempts (verified against tenacity 9.1.4), so the success tests fail the
callback 4 times and succeed on the 5th.
"""
from unittest.mock import patch

import pytest
import requests
import responses

from entities.managers import Arr, Bindery


@pytest.fixture
def arr():
    """Minimal Arr stub: bypass __init__'s Manager FK requirement."""
    a = Arr.__new__(Arr)
    a.apiurl = 'http://test-sonarr:8989/api/v3'
    a.headers = {'X-Api-Key': 'test', 'Accept': 'application/json'}
    return a


@pytest.fixture
def bindery():
    """Minimal Bindery stub: bypass __init__'s Manager FK requirement."""
    b = Bindery.__new__(Bindery)
    b.apiurl = 'http://test-bindery:8787/api/v1'
    b.headers = {'X-Api-Key': 'test-key', 'Accept': 'application/json'}
    return b


@pytest.fixture
def no_backoff():
    """Neutralize tenacity's real exponential backoff sleeps.

    tenacity.nap.sleep calls time.sleep; patching time.sleep for the test
    duration makes the retry loop deterministic and fast.
    """
    with patch('time.sleep'):
        yield


@responses.activate
def test_arr_test_retries_on_connection_error(arr, no_backoff):
    """PIPE-07: transient ConnectionErrors are absorbed; the 5th attempt succeeds.

    stop_after_attempt(5) caps the budget at 5 total attempts, so the
    callback fails 4 times and succeeds on the 5th.
    """
    call_count = {'n': 0}

    def callback(request):
        call_count['n'] += 1
        if call_count['n'] <= 4:
            raise requests.exceptions.ConnectionError("simulated")
        return (200, {}, '{"version": "1.0"}')

    responses.add_callback(
        responses.GET, 'http://test-sonarr:8989/api/v3/system/status',
        callback=callback,
    )
    success, body = arr.test()
    assert success is True
    assert call_count['n'] == 5, (
        f"expected 5 attempts (4 fails + 1 success), got {call_count['n']}"
    )


@responses.activate
def test_arr_test_reraises_original_exception_after_exhaustion(arr, no_backoff):
    """PIPE-07: after the 5-attempt budget is exhausted, the caller sees the
    original ConnectionError (not tenacity.RetryError) via the (False, msg)
    contract."""
    for _ in range(6):
        responses.add(
            responses.GET, 'http://test-sonarr:8989/api/v3/system/status',
            body=requests.exceptions.ConnectionError("permanent failure"),
        )
    success, msg = arr.test()
    assert success is False
    assert isinstance(msg, requests.exceptions.ConnectionError), (
        f"expected the original ConnectionError, got {type(msg).__name__}"
    )


@responses.activate
def test_bindery_test_retries_on_connection_error(bindery, no_backoff):
    """PIPE-07: the Bindery.test wrap site also absorbs transient
    ConnectionErrors."""
    call_count = {'n': 0}

    def callback(request):
        call_count['n'] += 1
        if call_count['n'] <= 4:
            raise requests.exceptions.ConnectionError("simulated")
        return (200, {}, '{"status": "ok"}')

    responses.add_callback(
        responses.GET, 'http://test-bindery:8787/api/v1/health',
        callback=callback,
    )
    success, body = bindery.test()
    assert success is True
    assert call_count['n'] == 5, (
        f"expected 5 attempts (4 fails + 1 success), got {call_count['n']}"
    )