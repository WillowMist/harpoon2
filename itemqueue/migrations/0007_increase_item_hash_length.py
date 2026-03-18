from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('itemqueue', '0006_item_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='hash',
            field=models.CharField(max_length=200, primary_key=True, serialize=False),
        ),
    ]
