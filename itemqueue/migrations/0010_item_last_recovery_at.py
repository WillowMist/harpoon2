# Hand-written additive migration adding Item.last_recovery_at.
#
# (a) Additive nullable column — no data migration needed. NULL means
#     "cooldown elapsed"; the state machine treats NULL as elapsed, so no
#     backfill is required.
# (b) db_index=True matches the planned check_stalled query path
#     (Q(last_recovery_at__isnull=True) | Q(last_recovery_at__lt=now-60s)).
# (c) Existing ItemHistory "Requeued by check_stalled_transfers" rows
#     become pure audit — do not read them post-Phase 5; last_recovery_at
#     is the single source of truth for the cooldown.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0009_file_transfer_unique_item_filename'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='last_recovery_at',
            field=models.DateTimeField(blank=True, db_index=True, help_text='Last time the recovery loop requeued this item; NULL means cooldown elapsed.', null=True),
        ),
    ]