"""Lock-in test for migration 0010 (Item.last_recovery_at).

Guards the additive-migration contract from Phase 5 RESEARCH.md §"Migration
plan": exactly one AddField on Item.last_recovery_at with
DateTimeField(null=True, blank=True, db_index=True), depending only on
0009, with no RunPython / RenameIndex / RemoveField operations.

Uses AST inspection (same pattern as test_filetransfer_uniqueness.py) so
comment text mentioning excluded operations can't false-positive.
"""
import ast
import os

from django.apps import apps

MIGRATION_NAME = "0010_item_last_recovery_at.py"


def _migration_path():
    itemqueue_app = apps.get_app_config("itemqueue")
    migrations_dir = os.path.dirname(itemqueue_app.module.__file__) + "/migrations"
    return os.path.join(migrations_dir, MIGRATION_NAME)


def _migration_tree():
    path = _migration_path()
    assert os.path.exists(path), f"Migration file not found at {path}"
    with open(path) as f:
        return ast.parse(f.read())


def _migration_class(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            return node
    raise AssertionError("Migration class not found")


def test_migration_0010_exists():
    """Migration 0010 adding Item.last_recovery_at must exist."""
    assert os.path.exists(_migration_path()), (
        f"Migration file not found at {_migration_path()}"
    )


def _get_addfield_call():
    """Return the single AddField call node in the migration, asserting
    there is exactly one."""
    tree = _migration_tree()
    migration_cls = _migration_class(tree)

    addfield_calls = []
    for node in ast.walk(migration_cls):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "AddField":
                addfield_calls.append(node)
    assert len(addfield_calls) == 1, (
        f"Migration 0010 must have exactly one AddField, found {len(addfield_calls)}"
    )
    return addfield_calls[0]


def test_migration_0010_has_exactly_one_addfield():
    """The migration must contain exactly one AddField operation."""
    _get_addfield_call()


def test_migration_0010_adds_last_recovery_at_datetimefield():
    """The AddField must target model_name='item', name='last_recovery_at',
    and field=models.DateTimeField(null=True, blank=True, db_index=True)."""
    addfield = _get_addfield_call()

    kwargs = {kw.arg: kw.value for kw in addfield.keywords if kw.arg is not None}

    # model_name='item'
    assert isinstance(kwargs.get("model_name"), ast.Constant), "model_name missing"
    assert kwargs["model_name"].value == "item", (
        f"AddField model_name must be 'item', got {kwargs['model_name'].value!r}"
    )

    # name='last_recovery_at'
    assert isinstance(kwargs.get("name"), ast.Constant), "name missing"
    assert kwargs["name"].value == "last_recovery_at", (
        f"AddField name must be 'last_recovery_at', got {kwargs['name'].value!r}"
    )

    # field=models.DateTimeField(...)
    field = kwargs.get("field")
    assert field is not None, "AddField missing field= keyword"
    assert isinstance(field, ast.Call) and isinstance(field.func, ast.Attribute), (
        "field= must be a models.DateTimeField(...) call"
    )
    assert field.func.attr == "DateTimeField", (
        f"field must be DateTimeField, got {field.func.attr!r}"
    )

    field_kwargs = {kw.arg: kw.value for kw in field.keywords if kw.arg is not None}
    for expected_arg, expected_value in (
        ("null", True),
        ("blank", True),
        ("db_index", True),
    ):
        assert expected_arg in field_kwargs, (
            f"DateTimeField missing {expected_arg}= keyword"
        )
        val = field_kwargs[expected_arg]
        assert isinstance(val, ast.Constant) and val.value is expected_value, (
            f"DateTimeField {expected_arg}= must be {expected_value}, "
            f"got {ast.dump(val)}"
        )


def test_migration_0010_depends_only_on_0009():
    """dependencies must reference ('itemqueue', '0009_file_transfer_unique_item_filename')
    and nothing else — no entities/users deps."""
    tree = _migration_tree()
    migration_cls = _migration_class(tree)

    deps = []
    for node in ast.walk(migration_cls):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "dependencies":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Tuple):
                                parts = []
                                for e in elt.elts:
                                    if isinstance(e, ast.Constant):
                                        parts.append(e.value)
                                deps.append(tuple(parts))

    assert deps == [("itemqueue", "0009_file_transfer_unique_item_filename")], (
        f"Migration 0010 dependencies must be exactly "
        f"[('itemqueue', '0009_file_transfer_unique_item_filename')], got {deps}"
    )


def test_migration_0010_is_purely_additive():
    """No RunPython, RenameIndex, RemoveField, or AlterField operations —
    the migration is additive (nullable column, no data migration)."""
    tree = _migration_tree()
    migration_cls = _migration_class(tree)

    op_types = set()
    for node in ast.walk(migration_cls):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            op_types.add(node.func.attr)

    excluded = {"RunPython", "RenameIndex", "RemoveField", "AlterField"}
    leaked = op_types & excluded
    assert not leaked, (
        f"Migration 0010 must NOT include {leaked} — it is purely additive."
    )