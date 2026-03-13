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
    apikey = models.CharField(max_length=50, blank=True, null=True)
    folder = models.ForeignKey(DownloadFolder, on_delete=models.CASCADE, null=True, blank=True)
    label = models.CharField(max_length=25, blank=True, null=True)

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


class Downloader(models.Model):
    name = models.CharField(max_length=30, unique=True)
    downloadertype = models.CharField(max_length=20, choices=DOWNLOADER_TYPES)
    options = models.JSONField(default=dict)
    seedbox = models.ForeignKey('Seedbox', on_delete=models.SET_NULL, null=True, blank=True, related_name='downloaders')

    @classmethod
    def from_db(cls, db, field_names, values):
        new = super(Downloader, cls).from_db(db, field_names, values)
        # cache value went from the base
        new.client = getattr(downloaders, new.downloadertype)(new)
        return new

    # @property
    # def client(self):
    #     return getattr(downloaders, self.downloadertype)(self)

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
