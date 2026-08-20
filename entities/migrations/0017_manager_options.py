# Added Manager.options JSONField for per-manager JSON configuration.
# Currently used by Bindery to store ebook/audiobook folder paths, the
# download-client path remap string, and the transient-error substring
# that distinguishes a retryable import failure from a terminal one.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('entities', '0016_increase_apikey_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='manager',
            name='options',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
