"""PIPE-07: tenacity retry behavior on the SFTP connect path.

Covers _sftp_connect_with_retry (dplibs/retry.py):
  - transient SSHExceptions are absorbed; the connect eventually succeeds
  - after the retry budget is exhausted, the original SSHException propagates
    (reraise=True contract — NOT tenacity.RetryError)

Note on attempt counts: stop_after_attempt(5) caps the budget at 5 TOTAL
attempts (verified against tenacity 9.1.4), so the success test fails the
connect 4 times and succeeds on the 5th.
"""
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from dplibs.retry import _sftp_connect_with_retry
from entities.models import Seedbox


@pytest.fixture
def seedbox():
    """Lightweight Seedbox stub: bypass the DB, set only the attrs the
    connect helper reads."""
    sb = Seedbox.__new__(Seedbox)
    sb.host = 'seedbox.test'
    sb.port = 22
    sb.username = 'test'
    sb.auth_type = 'password'
    sb.password = 'x'
    sb.ssh_key = None
    return sb


@pytest.fixture
def no_backoff():
    """Neutralize tenacity's real exponential backoff sleeps.

    tenacity.nap.sleep calls time.sleep; patching time.sleep for the test
    duration makes the retry loop deterministic and fast.
    """
    with patch('time.sleep'):
        yield


@patch('paramiko.SSHClient')
def test_sftp_connect_retries_on_ssh_exception(mock_ssh_cls, seedbox, no_backoff):
    """PIPE-07: transient SSHExceptions are absorbed; the 5th attempt succeeds.

    stop_after_attempt(5) caps the budget at 5 total attempts, so connect
    fails 4 times and succeeds on the 5th.
    """
    client = MagicMock()
    mock_ssh_cls.return_value = client
    call_count = {'n': 0}

    def connect(*args, **kwargs):
        call_count['n'] += 1
        if call_count['n'] <= 4:
            raise paramiko.ssh_exception.SSHException("simulated")
        return None

    client.connect.side_effect = connect
    result = _sftp_connect_with_retry(seedbox)
    assert result is client
    assert call_count['n'] == 5, (
        f"expected 5 attempts (4 fails + 1 success), got {call_count['n']}"
    )


@patch('paramiko.SSHClient')
def test_sftp_connect_reraises_original_exception_after_exhaustion(
    mock_ssh_cls, seedbox, no_backoff
):
    """PIPE-07: after the 5-attempt budget is exhausted, the original
    SSHException propagates (reraise=True — NOT tenacity.RetryError)."""
    client = MagicMock()
    mock_ssh_cls.return_value = client
    client.connect.side_effect = paramiko.ssh_exception.SSHException(
        "permanent failure"
    )
    with pytest.raises(paramiko.ssh_exception.SSHException):
        _sftp_connect_with_retry(seedbox)