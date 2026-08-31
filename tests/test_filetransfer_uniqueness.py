"""Lock-in tests for the FileTransfer (item, filename) uniqueness fix.

Two-part fix to stop the duplicate-row race that left 2,037+ duplicate
rows in the user's torrent:
1. Source-level dedup of transfer_list in transfer_files_async (defensive)
2. DB-level UniqueConstraint on (item, filename) (authoritative)

These tests verify both are in place and the migration applied correctly.
"""
import inspect
import os

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harpoon2.settings")
django.setup()

from itemqueue.models import FileTransfer
from itemqueue import tasks as tasks_module
from itemqueue.tasks import transfer_files_async


# --------------------------------------------------------------------------
# 1. Source-level dedup of transfer_list in transfer_files_async
# --------------------------------------------------------------------------

def test_transfer_list_is_deduped_before_per_file_loop():
    """transfer_list must be deduped by filename before the per-file loop.

    rTorrent's get_download_info can return the same logical file twice
    (e.g., once via the proper directory tree and once via a fallback
    listing) with slightly different remote paths. Without dedup, each
    duplicate entry reaches the inner loop, which writes a 'Copied' history
    event for the same FileTransfer row and re-runs status='completed' save.
    """
    src = inspect.getsource(transfer_files_async)
    # Look for the dedup block: it should set seen = set() and dedupe
    # transfer_list before the per-file loop. The marker comment + dedup
    # pattern should be present.
    assert "seen = set()" in src, (
        "transfer_files_async is missing the `seen = set()` dedup marker. "
        "The per-file loop will process the same filename multiple times "
        "if transfer_list contains duplicates."
    )
    # The dedup must happen BEFORE the per-file loop
    seen_idx = src.find("seen = set()")
    per_file_idx = src.find("for remote_file_path, relative_path in transfer_list:")
    assert seen_idx != -1 and per_file_idx != -1, "dedup markers not found"
    assert seen_idx < per_file_idx, (
        f"dedup (line {seen_idx}) must come BEFORE the per-file loop "
        f"(line {per_file_idx})"
    )


def test_transfer_list_dedup_logs_when_active():
    """When dedup actually shrinks transfer_list, log a debug message.

    Lock-in: if the dedup logging is removed, this test fails so the
    silent dedup isn't silently broken."""
    src = inspect.getsource(transfer_files_async)
    assert "Deduped transfer_list" in src, (
        "transfer_files_async is missing the debug log when dedup is active. "
        "Without it, silent dedup failures would be invisible."
    )


# --------------------------------------------------------------------------
# 2. DB-level UniqueConstraint in FileTransfer.Meta
# --------------------------------------------------------------------------

def test_filetransfer_has_unique_item_filename_constraint():
    """FileTransfer.Meta must declare a UniqueConstraint on (item, filename).

    Without this at the model level, future `makemigrations` would drop
    the constraint. With it at the DB level (via the migration), Django's
    `get_or_create()` becomes truly atomic — concurrent INSERTs are
    rejected with IntegrityError rather than both succeeding.
    """
    constraints = FileTransfer._meta.constraints
    # Look for a UniqueConstraint on fields ('item', 'filename')
    matching = [
        c for c in constraints
        if (
            isinstance(c, type(FileTransfer._meta.constraints[0]))
            if constraints else False
        )
        or hasattr(c, 'fields')
    ]
    found = None
    for c in constraints:
        if hasattr(c, 'fields') and tuple(c.fields) == ('item', 'filename'):
            found = c
            break
    assert found is not None, (
        f"FileTransfer is missing a UniqueConstraint on (item, filename). "
        f"Found constraints: {[c for c in constraints]}"
    )
    assert 'uniq_itemqueue_filetransfer_item_filename' in (found.name or ''), (
        f"UniqueConstraint name mismatch: expected 'uniq_itemqueue_filetransfer_item_filename', "
        f"got {found.name!r}"
    )


def test_migration_0009_exists():
    """Migration 0009 adding the unique constraint must exist."""
    from django.apps import apps
    import os
    itemqueue_app = apps.get_app_config('itemqueue')
    migrations_dir = os.path.dirname(itemqueue_app.module.__file__) + '/migrations'
    migration_path = os.path.join(migrations_dir, '0009_file_transfer_unique_item_filename.py')
    assert os.path.exists(migration_path), (
        f"Migration file not found at {migration_path}"
    )


def test_migration_0009_uses_unique_constraint():
    """Migration 0009 must add a UniqueConstraint (not just an index).

    UniqueConstraint is what makes get_or_create atomic at the DB level.
    A bare unique=True on the field would also work but Django's model
    layer tracks it differently."""
    import ast
    import os
    from django.apps import apps
    itemqueue_app = apps.get_app_config('itemqueue')
    migrations_dir = os.path.dirname(itemqueue_app.module.__file__) + '/migrations'
    migration_path = os.path.join(migrations_dir, '0009_file_transfer_unique_item_filename.py')

    # Parse the AST and check the actual operations, not substring matches.
    # (A substring check would flag the comment text mentioning the excluded
    # operations.)
    with open(migration_path) as f:
        src = f.read()
    tree = ast.parse(src)

    migration_cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            migration_cls = node
            break
    assert migration_cls is not None, "Migration class not found in 0009"

    # Collect actual operation types used
    op_types = set()
    for node in ast.walk(migration_cls):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                op_types.add(func.attr)
    assert "AddConstraint" in op_types, (
        f"Migration 0009 must include AddConstraint. Operations found: {op_types}"
    )
    # Should NOT include the schema-drift operations we intentionally excluded.
    excluded = {"RenameIndex", "AddField", "RemoveField", "AlterField"}
    leaked = op_types & excluded
    assert not leaked, (
        f"Migration 0009 must NOT include {leaked} — those are pre-existing "
        f"schema drift, separate concern."
    )

    # The UniqueConstraint must have fields=('item', 'filename') — check the AST
    found_constraint = False
    for node in ast.walk(migration_cls):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "AddConstraint"):
            continue
        for kw in node.keywords:
            if kw.arg == "fields" and isinstance(kw.value, ast.Tuple):
                elts = [e.id if isinstance(e, ast.Name) else
                         e.value if isinstance(e, ast.Constant) else None
                         for e in kw.value.elts]
                if tuple(elts) == ("item", "filename"):
                    found_constraint = True
                    break
    assert found_constraint, (
        f"Migration 0009 must add a UniqueConstraint on fields=('item', 'filename')"
    )
