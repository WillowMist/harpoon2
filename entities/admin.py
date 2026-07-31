from django.contrib import admin
from .models import DownloadFolder, Manager, Downloader, Seedbox, CachedDownloaderStatus


@admin.register(DownloadFolder)
class DownloadFolderAdmin(admin.ModelAdmin):
    list_display = ['folder', 'remote_folder_name']
    search_fields = ['folder']


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ['name', 'managertype', 'enabled', 'folder', 'torrent_downloader', 'nzb_downloader']
    list_filter = ['managertype', 'enabled']
    search_fields = ['name']
    fields = [
        'name', 'managertype', 'url', 'apikey', 'folder', 'label',
        'monitor_directory', 'monitor_subdirectories', 'category',
        'torrent_downloader', 'nzb_downloader', 'temp_folder',
        'poll_interval', 'move_on_complete', 'delete_source',
        'duplicate_handling', 'enabled', 'scan_on_startup',
    ]


@admin.register(Downloader)
class DownloaderAdmin(admin.ModelAdmin):
    list_display = ['name', 'downloadertype', 'seedbox']
    list_filter = ['downloadertype']
    search_fields = ['name']
    fields = ['name', 'downloadertype', 'options', 'seedbox']


@admin.register(Seedbox)
class SeedboxAdmin(admin.ModelAdmin):
    list_display = ['name', 'host', 'port', 'username', 'auth_type']
    search_fields = ['name', 'host', 'username']
    fields = ['name', 'host', 'port', 'username', 'auth_type', 'password', 'ssh_key', 'base_download_folder']


@admin.register(CachedDownloaderStatus)
class CachedDownloaderStatusAdmin(admin.ModelAdmin):
    list_display = ['downloader', 'last_updated']
    search_fields = ['downloader__name']
    readonly_fields = ['last_updated']
    fields = ['downloader', 'active_downloads', 'last_updated']
