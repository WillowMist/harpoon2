"""Verifies dplibs.filesystem.safe_rename is a thin wrapper around shutil.move
(COR-03: cross-device-safe rename). Runs without Django/DB so it's fast and
executable in any environment.
"""
import shutil
from unittest.mock import patch

from dplibs.filesystem import safe_rename


def test_safe_rename_is_shutil_move():
    """safe_rename(src, dst) must call shutil.move (cross-device safe),
    NOT os.rename (cross-device-broken)."""
    with patch('shutil.move', return_value='/dst') as mock_move:
        result = safe_rename('/src', '/dst')
    mock_move.assert_called_once_with('/src', '/dst')
    assert result == '/dst'


def test_safe_rename_passthrough(tmp_path):
    """End-to-end: safe_rename moves a file to a new path on the same FS."""
    src = tmp_path / 'src.txt'
    src.write_text('hello')
    dst = tmp_path / 'dst.txt'
    safe_rename(str(src), str(dst))
    assert dst.exists()
    assert dst.read_text() == 'hello'
