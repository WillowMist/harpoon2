# Hand-written additive migration adding the AirDC++ completion-check fields.
#
# (a) Additive nullable columns + an IntegerField with default=0 — no data
#     migration needed. Existing rows get NULL timers / empty path / count 0,
#     which the check_airdcpp_completions beat task treats as "not due".
# (b) next_check_at is db_index=True to keep the beat task's
#     (downloader__downloadertype='AirDC++', status='Grabbed',
#     next_check_at__lte=now) query indexed.
# (c) Reverse migration is RemoveField (no data loss — columns were additive).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0011_item_attempt_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='next_check_at',
            field=models.DateTimeField(blank=True, db_index=True, help_text='AirDC++ SFTP check time (nullable).', null=True),
        ),
        migrations.AddField(
            model_name='item',
            name='airdcpp_expected_path',
            field=models.CharField(blank=True, help_text='Expected file or folder name on AirDC++ share (nullable).', max_length=500, null=True),
        ),
        migrations.AddField(
            model_name='item',
            name='airdcpp_check_count',
            field=models.IntegerField(default=0, help_text='Number of AirDC++ completion checks performed (resets on success).'),
        ),
    ]