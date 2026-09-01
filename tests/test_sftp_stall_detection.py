"""Pin SFTP_STALL_TIMEOUT_SECONDS behavior: no-progress stall detection.

The previous implementation used a hard wall-clock timeout (300s) on the
whole sftp.get() call. Large 30-40GB files legitimately take longer
than that — the timeout fired mid-transfer even when the seedbox was
actively sending data.

This test pins the new behavior: the watchdog only fires when there's
no progress for the configured stall window, NOT after a fixed wall-
clock duration. Large actively-progressing transfers are allowed to
run indefinitely (subject to the 24h SFTP_MAX_TRANSFER_SECONDS cap).
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from itemqueue.tasks import _sftp_get_with_timeout, SFTPStallTimeout


def test_stall_detector_fires_on_no_progress():
    """sftp.get() makes one progress callback then stops. Watchdog should
    raise SFTPStallTimeout after the stall_timeout elapses with no further
    progress (NOT after a fixed wall-clock duration)."""
    sftp = MagicMock()
    callback = MagicMock()

    progress_state = {'count': 0, 'stop': False}

    def fake_get(remote, local, callback=None):
        # Emit one progress callback then go silent. Hold the thread
        # until the watchdog decides we've stalled.
        callback(100, 1_000_000)
        while not progress_state['stop']:
            time.sleep(0.1)
        # Real SFTP closes mid-call when we call sftp.close() from the
        # watchdog; emulate by raising.
        raise ConnectionError("channel closed by watchdog")

    sftp.get = fake_get

    # Tight stall_timeout (1s) so the test runs fast. The first callback
    # gives us a 1s grace; then no progress fires the detector.
    start = time.monotonic()
    with pytest.raises(SFTPStallTimeout) as exc_info:
        _sftp_get_with_timeout(sftp, '/remote', '/local', callback, stall_timeout=1)
    elapsed = time.monotonic() - start
    progress_state['stop'] = True  # let the worker thread exit

    # Watchdog should fire around 1s after the last progress callback,
    # not at any fixed wall-clock interval.
    assert 1.0 <= elapsed <= 3.0, f"expected ~1s stall detection, got {elapsed:.2f}s"
    msg = str(exc_info.value).lower()
    assert 'stall' in msg, f"exception should mention stall: {exc_info.value}"
    assert '/remote' in str(exc_info.value), f"exception should mention path"


def test_active_transfer_not_interrupted_by_stall_detector():
    """An actively-progressing transfer should NOT raise — only lack of
    progress triggers the watchdog. 30 callbacks over ~1s, with stall_timeout
    of 5s — the watchdog's poll loop will see fresh progress on every cycle."""
    sftp = MagicMock()
    callback = MagicMock()

    def fake_get(remote, local, callback=None):
        # Dense progress: 30 callbacks over ~1s (one every 33ms).
        for i in range(30):
            callback((i + 1) * 1000, 1_000_000)
            time.sleep(0.033)
        # Return cleanly.

    sftp.get = fake_get

    # stall_timeout=5 is much longer than the 33ms callback interval, so the
    # watchdog's poll loop sees fresh progress every cycle. The 1s total
    # runtime is well under the 5s stall threshold.
    start = time.monotonic()
    _sftp_get_with_timeout(sftp, '/remote', '/local', callback, stall_timeout=5)
    elapsed = time.monotonic() - start

    # Transfer completed without being stalled.
    assert elapsed < 4.0, f"active transfer shouldn't stall, but took {elapsed:.2f}s"
    assert callback.call_count >= 30, f"expected 30 progress callbacks, got {callback.call_count}"


def test_max_total_safety_cap():
    """Even with active progress, the max_total wall-clock cap fires as a
    safety net. Set max_total=1 second and run a transfer that keeps
    progressing for 2 seconds — should raise SFTPStallTimeout."""
    sftp = MagicMock()
    callback = MagicMock()

    progress_state = {'stop': False}

    def fake_get(remote, local, callback=None):
        # Active progress for ~3s.
        for i in range(15):
            if progress_state['stop']:
                return
            callback((i + 1) * 1000, 1_000_000)
            time.sleep(0.2)
        raise ConnectionError("unreachable")

    sftp.get = fake_get

    start = time.monotonic()
    with pytest.raises(SFTPStallTimeout):
        _sftp_get_with_timeout(sftp, '/remote', '/local', callback,
                               stall_timeout=10, max_total=1)
    elapsed = time.monotonic() - start
    progress_state['stop'] = True

    # max_total=1 means the cap fires around 1s.
    assert 1.0 <= elapsed <= 3.0, f"expected max_total cap at ~1s, got {elapsed:.2f}s"
