"""Lock-in test for migration 0011 (Item.attempt_count).

Guards the additive-migration contract from Phase 5 RESEARCH.md §"Migration
plan": exactly one AddField on Item.attempt_count with
IntegerField(default=0), depending only on 0010, with no RunPython /
RenameIndex / RemoveField / AlterField operations.

Uses AST inspection (same pattern as test_filetransfer_uniqueness.py and
test_migration_0010_last_recovery_at.py) so comment text mentioning excluded
operations can't false-positive.
"""
import ast
import os

from django.apps import apps

MIGRATION_NAME = "0011_item_attempt_count.py"


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


def test_migration_0011_exists():
    """Migration 0011 adding Item.attempt_count must exist."""
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
        f"Migration 0011 must have exactly one AddField, found {len(addfield_calls)}"
    )
    return addfield_calls[0]


def test_migration_0011_has_exactly_one_addfield():
    """The migration must contain exactly one AddField operation."""
    _get_addfield_call()


def test_migration_0011_adds_attempt_count_integerfield():
    """The AddField must target model_name='item', name='attempt_count',
    and field=models.IntegerField(default=0)."""
    addfield = _get_addfield_call()

    kwargs = {kw.arg: kw.value for kw in addfield.keywords if kw.arg is not None}

    # model_name='item'
    assert isinstance(kwargs.get("model_name"), ast.Constant), "model_name missing"
    assert kwargs["model_name"].value == "item", (
        f"AddField model_name must be 'item', got {kwargs['model_name'].value!r}"
    )

    # name='attempt_count'
    assert isinstance(kwargs.get("name"), ast.Constant), "name missing"
    assert kwargs["name"].value == "attempt_count", (
        f"AddField name must be 'attempt_count', got {kwargs['name'].value!r}"
    )

    # field=models.IntegerField(default=0)
    field = kwargs.get("field")
    assert field is not None, "AddField missing field= keyword"
    assert isinstance(field, ast.Call) and isinstance(field.func, ast.Attribute), (
        "field= must be a models.IntegerField(...) call"
    )
    assert field.func.attr == "IntegerField", (
        f"field must be IntegerField, got {field.func.attr!r}"
    )

    field_kwargs = {kw.arg: kw.value for kw in field.keywords if kw.arg is not None}
    assert "default" in field_kwargs, "IntegerField missing default= keyword"
    val = field_kwargs["default"]
    assert isinstance(val, ast.Constant) and val.value == 0, (
        f"IntegerField default= must be 0, got {ast.dump(val)}"
    )


def test_migration_0011_depends_only_on_0010():
    """dependencies must reference ('itemqueue', '0010_item_last_recovery_at')
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

    assert deps == [("itemqueue", "0010_item_last_recovery_at")], (
        f"Migration 0011 dependencies must be exactly "
        f"[('itemqueue', '0010_item_last_recovery_at')], got {deps}"
    )


def test_migration_0011_is_purely_additive():
    """No RunPython, RenameIndex, RemoveField, or AlterField operations —
    the migration is additive (default-0 column, no data migration)."""
    tree = _migration_tree()
    migration_cls = _migration_class(tree)

    op_types = set()
    for node in ast.walk(migration_cls):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            op_types.add(node.func.attr)

    excluded = {"RunPython", "RenameIndex", "RemoveField", "AlterField"}
    leaked = op_types & excluded
    assert not leaked, (
        f"Migration 0011 must NOT include {leaked} — it is purely additive."
    )