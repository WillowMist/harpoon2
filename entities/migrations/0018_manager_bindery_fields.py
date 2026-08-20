# Bindery-specific fields added to Manager. Each is a CharField, nullable
# to match the Blackhole fields' style. The Bindery manager class reads
# these directly from the model instance at runtime, instead of going
# through the JSON options field.
#
# Also a data migration that copies values from the legacy Manager.options
# JSONField into the new dedicated fields, so existing Bindery manager
# configurations don't lose their settings.

from django.db import migrations, models


def copy_bindery_options_to_fields(apps, schema_editor):
    Manager = apps.get_model('entities', 'Manager')
    for mgr in Manager.objects.filter(managertype='Bindery'):
        opts = mgr.options or {}
        if not isinstance(opts, dict):
            continue
        changed = False
        for src, dst in (
            ('ebook_folder', 'bindery_ebook_folder'),
            ('ebook_category', 'bindery_ebook_category'),
            ('audiobook_folder', 'bindery_audiobook_folder'),
            ('audiobook_category', 'bindery_audiobook_category'),
            ('path_remap', 'bindery_path_remap'),
            ('transient_error_substring', 'bindery_transient_error_substring'),
        ):
            val = opts.get(src)
            if val and not getattr(mgr, dst):
                setattr(mgr, dst, val)
                changed = True
        if changed:
            mgr.save(update_fields=[d for _, d in (
                ('ebook_folder', 'bindery_ebook_folder'),
                ('ebook_category', 'bindery_ebook_category'),
                ('audiobook_folder', 'bindery_audiobook_folder'),
                ('audiobook_category', 'bindery_audiobook_category'),
                ('path_remap', 'bindery_path_remap'),
                ('transient_error_substring', 'bindery_transient_error_substring'),
            )])


def noop_reverse(apps, schema_editor):
    # We don't move the values back into options on rollback - the JSONField
    # is preserved by the schema migration and operators can re-run the
    # data migration if needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('entities', '0017_manager_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='manager',
            name='bindery_ebook_folder',
            field=models.CharField(blank=True, help_text='Local staging root for ebooks. Falls back to manager.folder if unset.', max_length=500, null=True),
        ),
        migrations.AddField(
            model_name='manager',
            name='bindery_ebook_category',
            field=models.CharField(blank=True, help_text="SABnzbd / qBittorrent category for ebooks. Set on Bindery's download client; surfaced here for reference.", max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='manager',
            name='bindery_audiobook_folder',
            field=models.CharField(blank=True, help_text='Local staging root for audiobooks. Falls back to bindery_ebook_folder if unset.', max_length=500, null=True),
        ),
        migrations.AddField(
            model_name='manager',
            name='bindery_audiobook_category',
            field=models.CharField(blank=True, help_text="SABnzbd / qBittorrent category for audiobooks. Set on Bindery's download client; surfaced here for reference.", max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='manager',
            name='bindery_path_remap',
            field=models.CharField(blank=True, help_text='Path remap applied at the manual-import API call. Format: from:to,from2:to2. Example: /mnt/twilightsparkle/processing/downloads/bindery:/downloads', max_length=500, null=True),
        ),
        migrations.AddField(
            model_name='manager',
            name='bindery_transient_error_substring',
            field=models.CharField(blank=True, help_text='If Bindery\'s errorMessage contains this substring, the item is treated as PostProcessing (retryable) instead of Failed. Default: "the download may still be finishing".', max_length=255, null=True),
        ),
        migrations.RunPython(copy_bindery_options_to_fields, noop_reverse),
    ]

