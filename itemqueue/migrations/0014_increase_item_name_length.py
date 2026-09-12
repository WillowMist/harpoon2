from django.db import migrations, models


class Migration(migrations.Migration):
    """Widen itemqueue_item.name from VARCHAR(200) to VARCHAR(500).

    The Django model has long declared max_length=500 (itemqueue/models.py:7),
    but no migration was ever written to bump the live column past the 200
    set in 0001_initial. Result: filenames longer than 200 chars
    (e.g. ones dropped into a Blackhole subdirectory with rich metadata
    in the name) blow up with
    `psycopg2.errors.StringDataRightTruncation:
    value too long for type character varying(200)` on every poll.
    """

    dependencies = [
        ('itemqueue', '0013_filetransfer_status_index'),
    ]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='name',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
