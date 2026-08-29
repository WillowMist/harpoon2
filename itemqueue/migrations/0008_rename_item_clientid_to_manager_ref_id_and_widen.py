from django.db import migrations, models


def zero_to_null(apps, schema_editor):
    """Forward data step: set manager_ref_id = NULL where the row was 0.

    Historical Item.clientid = IntegerField(default=0) meant "no value" — every
    Item row that was never explicitly assigned a reference id carried 0. After
    the rename + widen to BigIntegerField(null=True, blank=True), NULL is the
    only correct way to express "no reference id" (per D-01). Converting the
    historical 0-valued rows to NULL preserves the semantic shift: rows that
    were never assigned still read as "no reference id".

    The reverse callable (null_to_zero) restores 0 for any NULL row so the
    migration is reversible.
    """
    Item = apps.get_model('itemqueue', 'Item')
    Item.objects.filter(manager_ref_id=0).update(manager_ref_id=None)


def null_to_zero(apps, schema_editor):
    """Reverse data step: set manager_ref_id = 0 for any NULL row.

    Required so `migrate itemqueue 0007` restores the old
    IntegerField(default=0) column shape with 0 as the no-value sentinel. One
    caveat (D-01): after a forward + reverse round-trip, historical "no value"
    rows are still 0, but any row that was explicitly assigned 0 (extremely
    unlikely — Bindery and arr queue IDs are always positive) loses that
    distinction. Acceptable because Bindery/arr queue IDs are always positive.
    """
    Item = apps.get_model('itemqueue', 'Item')
    Item.objects.filter(manager_ref_id__isnull=True).update(manager_ref_id=0)


class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0007_increase_item_hash_length'),
    ]

    operations = [
        # Step 1: rename (also renames the SQL column by default — db_column
        # is unset on the source field, so RenameField cascades to the column
        # name as well). Satisfies D-02: the SQL column is renamed to
        # manager_ref_id in lockstep with the Python attribute.
        migrations.RenameField(
            model_name='item',
            old_name='clientid',
            new_name='manager_ref_id',
        ),
        # Step 2: widen to 64-bit + allow NULL + drop the default. The old
        # IntegerField(default=0) overflows around 2.1B; Bindery bookIds and
        # arr queue ids can grow beyond that over years of operation.
        # BigIntegerField(null=True, blank=True) matches the existing pattern
        # on the neighboring Item.size / Item.received fields.
        migrations.AlterField(
            model_name='item',
            name='manager_ref_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        # Step 3: migrate the historical 0-sentinel rows to NULL. The data step
        # runs after the schema step so the column is NULL-able before we
        # attempt to set it. Single transaction on SQLite + PostgreSQL (DDL
        # transactions supported by both).
        migrations.RunPython(zero_to_null, null_to_zero),
    ]
