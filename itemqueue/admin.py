from django.contrib import admin
from .models import Item, ItemHistory, FileTransfer


class ItemHistoryInline(admin.TabularInline):
    model = ItemHistory
    extra = 0
    max_num = 20
    can_delete = True
    ordering = ['-created']
    readonly_fields = ['created', 'details']
    fields = ['created', 'details']


class FileTransferInline(admin.TabularInline):
    model = FileTransfer
    extra = 0
    max_num = 20
    can_delete = True
    ordering = ['-created']
    readonly_fields = ['created', 'modified']
    fields = ['filename', 'file_size', 'bytes_transferred', 'status', 'started', 'completed', 'error_message', 'remote_path', 'local_path']


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'status', 'downloader', 'manager', 'size', 'created', 'modified', 'archived']
    list_filter = ['status', 'downloader', 'manager', 'archived', 'created']
    search_fields = ['name', 'hash']
    readonly_fields = ['hash', 'created', 'modified']
    fields = [
        'name', 'hash', 'status', 'downloader', 'manager',
        'size', 'received', 'clientid', 'category',
        'extraction_status', 'extraction_progress', 'extraction_started', 'extraction_completed',
        'archived', 'archived_at', 'created', 'modified',
    ]
    inlines = [FileTransferInline, ItemHistoryInline]
    list_per_page = 50
    date_hierarchy = 'created'


@admin.register(ItemHistory)
class ItemHistoryAdmin(admin.ModelAdmin):
    list_display = ['item', 'created', 'details']
    search_fields = ['item__name', 'details']
    list_filter = ['created']
    readonly_fields = ['created']
    list_per_page = 50


@admin.register(FileTransfer)
class FileTransferAdmin(admin.ModelAdmin):
    list_display = ['item', 'filename', 'status', 'file_size', 'bytes_transferred', 'started', 'completed']
    list_filter = ['status', 'created']
    search_fields = ['item__name', 'filename', 'remote_path', 'local_path']
    readonly_fields = ['created', 'modified']
    list_per_page = 50
