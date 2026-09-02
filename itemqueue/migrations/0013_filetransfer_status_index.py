# Hand-written additive migration adding indexes on FileTransfer.status.
#
# (a) db_index=True on FileTransfer.status — Django represents this as an
#     AlterField (the field's deconstruction carries db_index=True), which
#     creates the single-column index. Speeds up the dashboard's
#     status__in=['pending', 'transferring'] scan.
# (b) Composite (item, status) index — speeds up the per-item transfer list
#     filter in api_queue for PostProcessing items. Name kept under Django's
#     30-char index-name limit.
# (c) Reverse migration is AlterField back to db_index=False + RemoveIndex
#     (indexes were additive; no data loss).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0012_item_airdcpp_check'),
    ]

    operations = [
        migrations.AlterField(
            model_name='filetransfer',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending'), ('transferring', 'Transferring'), ('completed', 'Completed'), ('failed', 'Failed')], db_index=True, default='pending', max_length=20),
        ),
        migrations.AddIndex(
            model_name='filetransfer',
            index=models.Index(fields=['item', 'status'], name='itemqueue_filetr_item_status'),
        ),
    ]