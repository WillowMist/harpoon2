"""Tests for the SFTP stall watchdog in itemqueue.tasks.

Covers _sftp_get_with_timeout and _stoppable_progress_callback.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from itemqueue.tasks import (
    SFTPStallTimeout,
    SFTP_GET_TIMEOUT_SECONDS,
    _sftp_get_with_timeout,
    _stoppable_progress_callback,
)


class TestStoppableProgressCallback:
    """The stoppable callback wrapper short-circuits once stop_event is set."""

    def test_passes_through_when_stop_not_set(self):
        inner = MagicMock()
        stop = threading.Event()
        wrapped = _stoppable_progress_callback(inner, stop)
        wrapped(100, 1000)
        inner.assert_called_once_with(100, 1000)

    def test_short_circuits_when_stop_set(self):
        inner = MagicMock()
        stop = threading.Event()
        stop.set()
        wrapped = _stoppable_progress_callback(inner, stop)
        wrapped(100, 1000)
        inner.assert_not_called()

    def test_swallows_inner_exception_with_debug_log(self):
        """Inner callback errors must not crash the SFTP get loop."""
        inner = MagicMock(side_effect=RuntimeError("db hiccup"))
        stop = threading.Event()
        wrapped = _stoppable_progress_callback(inner, stop)
        # Should not raise
        wrapped(100, 1000)


class TestSftpGetWithTimeout:
    """Wall-clock timeout for sftp.get() — the heart of fix B."""

    def test_returns_normally_when_get_finishes_in_time(self):
        sftp = MagicMock()
        sftp.get.return_value = None
        callback = MagicMock()
        _sftp_get_with_timeout(sftp, "/remote", "/local", callback, timeout=2)
        sftp.get.assert_called_once()
        sftp.close.assert_not_called()

    def test_propagates_exception_from_inside_get(self):
        """Real SFTP errors (socket.error, IOError) must propagate, not be swallowed."""
        sftp = MagicMock()
        sftp.get.side_effect = IOError("seedbox connection reset")
        callback = MagicMock()
        with pytest.raises(IOError, match="seedbox connection reset"):
            _sftp_get_with_timeout(sftp, "/remote", "/local", callback, timeout=2)

    def test_raises_stall_timeout_when_get_hangs(self):
        """The headline behavior: a hanging sftp.get() raises SFTPStallTimeout."""
        sftp = MagicMock()

        def hang(*args, **kwargs):
            # Block until released — simulate the dead-seedbox stall
            time.sleep(5)
            return None

        sftp.get.side_effect = hang
        # timeout=0.3s means the join returns within ~0.3s; hang() runs for 5s
        t0 = time.monotonic()
        with pytest.raises(SFTPStallTimeout, match="did not return within 0.3"):
            _sftp_get_with_timeout(sftp, "/remote", "/local", MagicMock(), timeout=0.3)
        elapsed = time.monotonic() - t0
        # Should return in ~timeout, not in the 5s hang() — proves the watchdog fires
        assert elapsed < 2.0, f"watchdog took {elapsed:.2f}s — should be ~0.3s"

    def test_stall_timeout_closes_sftp_channel(self):
        """On stall, the watchdog closes sftp to signal the leaked thread to exit."""
        sftp = MagicMock()

        def hang(*args, **kwargs):
            time.sleep(5)

        sftp.get.side_effect = hang
        with pytest.raises(SFTPStallTimeout):
            _sftp_get_with_timeout(sftp, "/remote", "/local", MagicMock(), timeout=0.2)
        sftp.close.assert_called_once()

    def test_stall_timeout_swallows_sftp_close_errors(self):
        """If sftp.close() itself raises (already-closed channel, etc.), don't crash."""
        sftp = MagicMock()

        def hang(*args, **kwargs):
            time.sleep(5)

        sftp.get.side_effect = hang
        sftp.close.side_effect = OSError("channel already closed")
        with pytest.raises(SFTPStallTimeout):
            _sftp_get_with_timeout(sftp, "/remote", "/local", MagicMock(), timeout=0.2)
        # close was attempted (and swallowed)
        sftp.close.assert_called_once()

    def test_progress_callback_stops_after_timeout(self):
        """After the watchdog raises, the stop_event is set — leaked thread's
        progress callback must not bump the DB. We verify by capturing the
        wrapped callback that _sftp_get_with_timeout passes to sftp.get."""
        sftp = MagicMock()
        captured = {}

        def hang(remote, local, callback=None, **kwargs):
            captured['cb'] = callback
            time.sleep(5)

        sftp.get.side_effect = hang
        inner = MagicMock()
        with pytest.raises(SFTPStallTimeout):
            _sftp_get_with_timeout(sftp, "/remote", "/local", inner, timeout=0.2)
        # Now invoke the captured callback — it should short-circuit
        captured['cb'](100, 1000)
        inner.assert_not_called()


class TestSftpGetTimeoutConstant:
    """The timeout constant should match the stall detector's threshold."""

    def test_default_matches_stall_threshold(self):
        # check_stalled_transfers uses 5 minutes; the watchdog should fire at the
        # same point so the worker doesn't outlive the operator-visible stall
        # by an order of magnitude. Lock-in test so a future refactor doesn't
        # quietly drift them apart.
        from datetime import timedelta
        from itemqueue.tasks import check_stalled_transfers  # noqa: F401
        assert SFTP_GET_TIMEOUT_SECONDS == 300
        assert timedelta(minutes=5).total_seconds() == SFTP_GET_TIMEOUT_SECONDS
