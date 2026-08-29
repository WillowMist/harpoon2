"""Filesystem helpers shared across apps.

Mirrors the dplibs/session.py / dplibs/search.py one-file-per-concern
convention: anything app-agnostic that touches the local filesystem
lives here.
"""
import shutil


def safe_rename(src, dst):
    """Cross-device-safe rename.

    shutil.move uses os.rename() on the same filesystem (atomic) and
    falls back to copy + delete when src and dst are on different
    filesystems. Replaces bare os.rename() calls in itemqueue/tasks.py
    that raised OSError: [Errno 18] Invalid cross-device link when
    temp_folder and final_folder are on different docker volumes.
    """
    return shutil.move(src, dst)
