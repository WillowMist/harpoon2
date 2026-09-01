"""Tests for the SFTP stall watchdog in itemqueue.tasks.

Covers _sftp_get_with_timeout and _stoppable_progress_callback. The
watchdog fires on NO-PROGRESS (configurable via SFTP_STALL_TIMEOUT_SECONDS,
default 180s) rather than a wall-clock cap on the whole transfer — so
large 30-40GB files can run as long as they're actively progressing.
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from itemqueue.tasks import (
    SFTPStallTimeout,
    SFTP_STALL_TIMEOUT_SECONDS,
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
    """Stall-detection watchdog for sftp.get() — fires on no-progress."""

    def test_returns_normally_when_get_finishes_with_progress(self):
        """An sftp.get() that completes AND reports progress returns normally."""
        sftp = MagicMock()
        progress_state = {'done': False}

        def fake_get(remote, local, callback=None):
            # Emit one progress callback then return.
            callback(100, 1_000_000)
            progress_state['done'] = True

        sftp.get = fake_get
        callback = MagicMock()
        _sftp_get_with_timeout(sftp, '/remote', '/local', callback, stall_timeout=2)
        assert progress_state['done']
        sftp.close.assert_not_called()
        callback.assert_called_once()

    def test_propagates_exception_from_inside_get(self):
        """Real SFTP errors (socket.error, IOError) must propagate, not be swallowed."""
        sftp = MagicMock()
        sftp.get = MagicMock(side_effect=IOError('seedbox connection reset'))
        with pytest.raises(IOError, match='seedbox connection reset'):
            _sftp_get_with_timeout(sftp, '/remote', '/local', MagicMock(), stall_timeout=2)

    def test_raises_stall_timeout_when_get_hangs_without_progress(self):
        """The headline behavior: a hanging sftp.get() with NO progress raises SFTPStallTimeout."""
        sftp = MagicMock()

        def hang(*args, **kwargs):
            # Block forever, never call the progress callback — simulates a
            # dead seedbox where the SSH channel is alive but no data flows.
            time.sleep(5)
            return None

        sftp.get = hang
        # stall_timeout=0.5s. The watchdog polls every 1s but the FIRST
        # poll at t=1 already has elapsed_stall=1.0s > 0.5s, so it fires
        # immediately on the first cycle.
        t0 = time.monotonic()
        with pytest.raises(SFTPStallTimeout, match='stalled'):
            _sftp_get_with_timeout(sftp, '/remote', '/local', MagicMock(), stall_timeout=0.5)
        elapsed = time.monotonic() - t0
        # Should fire within ~1s (one poll cycle), not 5s.
        assert elapsed < 3.0, f'watchdog took {elapsed:.2f}s — should be ~1s'

    def test_stall_timeout_closes_sftp_channel(self):
        """On stall, the watchdog closes sftp to signal the leaked thread to exit."""
        sftp = MagicMock()

        def hang(*args, **kwargs):
            time.sleep(5)

        sftp.get = hang
        with pytest.raises(SFTPStallTimeout):
            _sftp_get_with_timeout(sftp, '/remote', '/local', MagicMock(), stall_timeout=0.3)
        sftp.close.assert_called_once()

    def test_stall_timeout_swallows_sftp_close_errors(self):
        """If sftp.close() itself raises (already-closed channel, etc.), don't crash."""
        sftp = MagicMock()

        def hang(*args, **kwargs):
            time.sleep(5)

        sftp.get = hang
        sftp.close.side_effect = OSError('channel already closed')
        with pytest.raises(SFTPStallTimeout):
            _sftp_get_with_timeout(sftp, '/remote', '/local', MagicMock(), stall_timeout=0.3)
        # close was attempted (and swallowed)
        sftp.close.assert_called_once()

    def test_progress_callback_stops_after_stall(self):
        """After the watchdog raises, the stop_event is set — the leaked thread's
        progress callback must not bump the inner callback. We verify by capturing
        the wrapped callback that _sftp_get_with_timeout passes to sftp.get."""
        sftp = MagicMock()
        captured = {}

        def hang(remote, local, callback=None, **kwargs):
            captured['cb'] = callback
            time.sleep(5)

        sftp.get = hang
        inner = MagicMock()
        with pytest.raises(SFTPStallTimeout):
            _sftp_get_with_timeout(sftp, '/remote', '/local', inner, stall_timeout=0.3)
        # Now invoke the captured callback — it should short-circuit
        captured['cb'](100, 1000)
        inner.assert_not_called()


class TestSftpGetTimeoutConstant:
    """The SFTP stall timeout defaults to 3 minutes (180s) of no-progress."""

    def test_default_is_three_minutes(self):
        from datetime import timedelta
        assert SFTP_STALL_TIMEOUT_SECONDS == 180
        assert timedelta(minutes=3).total_seconds() == SFTP_STALL_TIMEOUT_SECONDS
