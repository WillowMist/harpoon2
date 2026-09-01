# Hand-written additive migration adding Item.attempt_count.
#
# (a) Additive IntegerField with default=0 — no data migration needed.
#     New column starts at 0 for every existing row.
# (b) PIPE-01 hard cap = 3; the column is the source of truth for the
#     retry_postprocessing attempt counter (survives worker restarts).
# (c) Reverse migration is RemoveField (no data loss — column was additive
#     with default=0).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0010_item_last_recovery_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='attempt_count',
            field=models.IntegerField(default=0, help_text='retry_postprocessing attempts; PIPE-01 hard cap = 3.'),
        ),
    ]