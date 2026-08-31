"""Lock-in test for the per-file atomic get_or_create fix in transfer_files_async.

The check-then-create race in transfer_files_async left 2,037 duplicate
FileTransfer rows for a 120 GB torrent (inflated the dashboard to 480
GB). Both the 'completed' (file exists locally with right size) and
'pending' (default) branches used:

    existing = FileTransfer.objects.filter(item=..., filename=...).first()
    if not existing:
        FileTransfer.objects.create(...)  # race window

Two concurrent tasks would both see "no existing" and both create.
get_or_create is atomic at the row level — only one task can succeed.

These tests verify both branches use get_or_create and have NOT
regressed to the bare-create pattern.
"""
import inspect
import re

from itemqueue.tasks import transfer_files_async


def test_completed_branch_uses_get_or_create():
    """The 'file already on disk with right size' branch must use get_or_create.

    Locked in to prevent a regression to bare FileTransfer.objects.create(),
    which re-introduces the duplicate-row race."""
    src = inspect.getsource(transfer_files_async)
    # The completed-branch create call is preceded by the comment
    # 'a completed FileTransfer record exists'
    completed_idx = src.find("a completed FileTransfer record exists")
    assert completed_idx != -1, "completed-branch marker not found"
    window = src[completed_idx:completed_idx + 1500]
    assert "FileTransfer.objects.get_or_create(" in window, (
        "completed-branch create is not using get_or_create — race condition "
        "can recreate duplicate rows. Use FileTransfer.objects.get_or_create()."
    )
    # The bare create pattern must NOT be present in this window
    assert "FileTransfer.objects.create(" not in window, (
        "completed-branch create is using bare .create() — race window. "
        "Use get_or_create to atomically insert-or-fetch."
    )


def test_pending_branch_uses_get_or_create():
    """The default 'pending'-status branch must use get_or_create.

    Same race-condition rationale as the completed branch."""
    src = inspect.getsource(transfer_files_async)
    # The pending-branch create is preceded by 'Create FileTransfer record'
    pending_idx = src.find("Create FileTransfer record with 'pending' status")
    assert pending_idx != -1, "pending-branch marker not found"
    window = src[pending_idx:pending_idx + 1500]
    assert "FileTransfer.objects.get_or_create(" in window, (
        "pending-branch create is not using get_or_create — race window."
    )
    assert "FileTransfer.objects.create(" not in window, (
        "pending-branch create is using bare .create() — race window."
    )


def test_no_bare_create_remaining():
    """Lock-in: NO `FileTransfer.objects.create(` call should remain in
    transfer_files_async. Both branches were migrated to get_or_create."""
    src = inspect.getsource(transfer_files_async)
    # Strip out any false positives from comments/docstrings
    bare_creates = re.findall(r"FileTransfer\.objects\.create\(", src)
    # The model has its own FileTransfer.objects.create in ItemTransfer
    # .save() — that's outside transfer_files_async so we don't count it.
    assert len(bare_creates) == 0, (
        f"Found {len(bare_creates)} bare `FileTransfer.objects.create(` "
        f"calls in transfer_files_async — both branches should use "
        f"get_or_create to prevent the duplicate-row race."
    )


def test_get_or_create_lookup_keys_are_item_and_filename():
    """The race-condition window was triggered by `item, filename` being
    the natural unique key (each file should map to one row per item).
    The get_or_create must look up by exactly these two fields so the
    DB-level lookup catches the race correctly."""
    src = inspect.getsource(transfer_files_async)
    # Find every get_or_create call
    occurrences = src.count("FileTransfer.objects.get_or_create(")
    assert occurrences == 2, (
        f"Expected 2 get_or_create calls in transfer_files_async "
        f"(completed + pending branches), found {occurrences}"
    )
    # Each one must look up by item= and filename=
    for m in re.finditer(
        r"FileTransfer\.objects\.get_or_create\(([^)]+)\)", src, re.DOTALL
    ):
        kwargs = m.group(1)
        assert "item=item" in kwargs, (
            f"get_or_create missing item=item lookup: {kwargs[:200]}"
        )
        assert "filename=" in kwargs, (
            f"get_or_create missing filename= lookup: {kwargs[:200]}"
        )
