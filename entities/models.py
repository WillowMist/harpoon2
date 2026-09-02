from django.db import models
import os
from harpoon2.settings import MANAGER_TYPES, DOWNLOADER_TYPES
from . import managers, downloaders

# Create your models here.


class DownloadFolder(models.Model):
    folder = models.CharField(max_length=400, unique=True)
    remote_folder_name = models.CharField(max_length=400, blank=True, null=True)

    def __str__(self):
        return self.folder


class Manager(models.Model):
    name = models.CharField(max_length=30, unique=True)
    managertype = models.CharField(max_length=20, choices=MANAGER_TYPES)
    url = models.URLField(blank=True, null=True)
    apikey = models.CharField(max_length=100, blank=True, null=True)
    folder = models.ForeignKey(DownloadFolder, on_delete=models.CASCADE, null=True, blank=True)
    label = models.CharField(max_length=25, blank=True, null=True)

    # Per-manager-type JSON configuration. Currently used by Bindery to store
    # ebook/audiobook folder paths, download-client path remap, and recovery
    # transient-error substrings. Empty dict for managers that don't need it.
    options = models.JSONField(default=dict, blank=True)

    # Bindery-specific fields. These are used by the Bindery manager class
    # when populating its JSON options at runtime. The form renders them
    # as individual fields so values can be edited and persisted directly
    # (no JS serialization layer required).
    bindery_ebook_folder = models.CharField(
        max_length=500, blank=True, null=True,
        help_text='Local staging root for ebooks. Falls back to manager.folder if unset.'
    )
    bindery_ebook_category = models.CharField(
        max_length=100, blank=True, null=True,
        help_text='SABnzbd / qBittorrent category for ebooks. Set on Bindery\'s download client; surfaced here for reference.'
    )
    bindery_audiobook_folder = models.CharField(
        max_length=500, blank=True, null=True,
        help_text='Local staging root for audiobooks. Falls back to bindery_ebook_folder if unset.'
    )
    bindery_audiobook_category = models.CharField(
        max_length=100, blank=True, null=True,
        help_text='SABnzbd / qBittorrent category for audiobooks. Set on Bindery\'s download client; surfaced here for reference.'
    )
    bindery_path_remap = models.CharField(
        max_length=500, blank=True, null=True,
        help_text='Path remap applied at the manual-import API call. Format: from:to,from2:to2. Example: /mnt/processing/downloads/bindery:/downloads'
    )
    bindery_transient_error_substring = models.CharField(
        max_length=255, blank=True, null=True,
        help_text='If Bindery\'s errorMessage contains this substring, the item is treated as PostProcessing (retryable) instead of Failed. Default: "the download may still be finishing".'
    )

    # Blackhole-specific fields
    monitor_directory = models.CharField(max_length=500, blank=True, null=True, help_text="Directory to monitor for .nzb and .torrent files")
    monitor_subdirectories = models.BooleanField(default=False, help_text="Monitor subdirectories for category detection")
    category = models.CharField(max_length=50, blank=True, null=True, help_text="Category to assign when not monitoring subdirectories")
    torrent_downloader = models.ForeignKey('Downloader', on_delete=models.SET_NULL, null=True, blank=True, related_name='torrent_managers', help_text="Torrent client for .torrent files")
    nzb_downloader = models.ForeignKey('Downloader', on_delete=models.SET_NULL, null=True, blank=True, related_name='nzb_managers', help_text="NZB client for .nzb files")
    temp_folder = models.CharField(max_length=500, blank=True, null=True, help_text="Temporary folder for incomplete downloads")
    poll_interval = models.IntegerField(default=60, help_text="Seconds between directory scans")
    move_on_complete = models.BooleanField(default=True, help_text="Move files to destination (vs copy)")
    delete_source = models.BooleanField(default=True, help_text="Delete source .nzb/.torrent file after sending to downloader")
    duplicate_handling = models.CharField(max_length=10, choices=[('skip', 'Skip'), ('rename', 'Rename'), ('overwrite', 'Overwrite')], default='skip', help_text="How to handle duplicate files")
    enabled = models.BooleanField(default=True, help_text="Enable/disable monitoring")
    scan_on_startup = models.BooleanField(default=True, help_text="Process existing files on startup")

    @classmethod
    def from_db(cls, db, field_names, values):
        new = super(Manager, cls).from_db(db, field_names, values)
        # cache value went from the base
        new.client = getattr(managers, new.managertype)(new)
        return new
    
    def post_process(self, item, download_path):
        """Call post_process on the appropriate manager class.
        
        This method delegates to the actual manager class (Sonarr, Radarr, etc.)
        keeping manager logic compartmentalized in each class.
        """
        if self.client and hasattr(self.client, 'post_process'):
            return self.client.post_process(item, download_path)
        else:
            return False, f"Manager {self.name} does not support post_process"


class Downloader(models.Model):
    name = models.CharField(max_length=30, unique=True)
    downloadertype = models.CharField(max_length=20, choices=DOWNLOADER_TYPES)
    options = models.JSONField(default=dict)
    seedbox = models.ForeignKey('Seedbox', on_delete=models.SET_NULL, null=True, blank=True, related_name='downloaders')

    def __str__(self):
        return self.name

    @classmethod
    def from_db(cls, db, field_names, values):
        new = super(Downloader, cls).from_db(db, field_names, values)
        # The client is no longer built eagerly here — see the `client`
        # property below. Building it in from_db meant every ORM fetch of a
        # Downloader row (including select_related on a FK in a web view)
        # triggered a synchronous seedbox network call; the RTorrent
        # constructor does a live XML-RPC round-trip, so /api/dashboard/
        # polls were blocking I/O waits inside gunicorn worker threads.
        return new

    @property
    def client(self):
        """Lazily construct the downloader client on first access.

        Returning a property instead of eagerly building one in from_db means
        merely fetching a Downloader row (e.g. select_related on a FK in a
        web view) no longer triggers a synchronous seedbox network call.
        The RTorrent client constructor does a live XML-RPC round-trip to
        fetch the server version, so an eager constructor turned every
        /api/dashboard/ poll into a blocking I/O wait inside a gunicorn
        worker thread. Callers that need the client still get one; callers
        that only need the row's fields (name, downloadertype, options) pay
        nothing.
        """
        from . import downloaders
        downloader_attr = downloaders.DOWNLOADER_NAME_MAP.get(self.downloadertype, self.downloadertype)
        return getattr(downloaders, downloader_attr)(self)

    def checkoptions(self):
        optionfields = self.client.optionfields
        for fieldname in optionfields.keys():
            if fieldname not in self.options.keys():
                if optionfields[fieldname] == 'string':
                    self.options[fieldname] = ''
                elif optionfields[fieldname] == 'int':
                    self.options[fieldname] = 0
                elif optionfields[fieldname] == 'boolean':
                    self.options[fieldname] = False
        self.save()

    def test(self):
        if hasattr(self, 'client'):
            if self.client.reload:
                self.client.__init__(self)
            return self.client.test()


class Seedbox(models.Model):
    AUTH_TYPE_CHOICES = [
        ('password', 'Username/Password'),
        ('key', 'SSH Key'),
    ]
    
    name = models.CharField(max_length=30, unique=True)
    host = models.CharField(max_length=255)
    port = models.IntegerField(default=22)
    username = models.CharField(max_length=100)
    auth_type = models.CharField(max_length=10, choices=AUTH_TYPE_CHOICES, default='password')
    password = models.CharField(max_length=255, blank=True, null=True)
    ssh_key = models.TextField(blank=True, null=True)
    base_download_folder = models.CharField(max_length=400, blank=True, null=True, help_text="Base path on seedbox for downloads (e.g., /home/user/downloads)")

    def __str__(self):
        return self.name


class CachedDownloaderStatus(models.Model):
    """Cache for downloader API responses to speed up page loads."""
    downloader = models.ForeignKey(Downloader, on_delete=models.CASCADE, related_name='cached_status')
    active_downloads = models.JSONField(default=list)
    last_updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = 'Cached downloader statuses'
    
    def __str__(self):
        return f"{self.downloader.name} - {self.last_updated}"
