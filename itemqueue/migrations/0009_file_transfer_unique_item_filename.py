# Hand-written migration to apply ONLY the unique constraint added in
# itemqueue/models.py's FileTransfer.Meta. Other "drift" operations Django's
# makemigrations picked up (RenameIndex for indexes that don't exist in
# production, AddField for category that the DB has but Django doesn't know
# about) are intentionally omitted here — they need their own focused
# migrations to avoid breaking on the drifted prod schema.
#
# Pre-flight: the user ran a one-shot dedup script via ssh docker that
# removed duplicate FileTransfer rows, so the constraint can be added
# cleanly without violating uniqueness.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0008_rename_item_clientid_to_manager_ref_id_and_widen'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='filetransfer',
            constraint=models.UniqueConstraint(
                fields=('item', 'filename'),
                name='uniq_itemqueue_filetransfer_item_filename',
            ),
        ),
    ]
