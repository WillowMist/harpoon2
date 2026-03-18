from django.db import models
from django.db.models import Q
import watson

# Create your models here.

class Item(models.Model):
    name = models.CharField(max_length=200, default='', blank=True)
    hash = models.CharField(max_length=200, primary_key=True)
    manager = models.ForeignKey('entities.Manager', on_delete=models.CASCADE, null=True, blank=True)
    downloader = models.ForeignKey('entities.Downloader', on_delete=models.CASCADE, null=True, blank=True)
    size = models.BigIntegerField(default=0)
    received = models.BigIntegerField(default=0)
    status = models.CharField(max_length=50, default='Created', db_index=True)
    clientid = models.IntegerField(default=0)
    category = models.CharField(max_length=100, default='', blank=True)  # Category/label from downloader
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    
    # Extraction tracking
    extraction_status = models.CharField(max_length=50, default='', blank=True)  # 'extracting', 'completed', 'failed', etc
    extraction_progress = models.IntegerField(default=0)  # 0-100%
    extraction_started = models.DateTimeField(null=True, blank=True)
    extraction_completed = models.DateTimeField(null=True, blank=True)
    
    # Archive tracking
    archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'archived', '-modified']),
            models.Index(fields=['status', 'archived', '-archived_at']),
        ]


class ItemHistory(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='history')
    created = models.DateTimeField(auto_now_add=True)
    details = models.CharField(max_length=200)


class FileTransfer(models.Model):
    """Track SFTP file transfers from seedbox to local storage."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('transferring', 'Transferring'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='transfers')
    filename = models.CharField(max_length=300)
    remote_path = models.CharField(max_length=500)
    local_path = models.CharField(max_length=500)
    file_size = models.BigIntegerField(default=0)  # Total size in bytes
    bytes_transferred = models.BigIntegerField(default=0)  # Progress
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)  # Updated whenever bytes_transferred changes
    started = models.DateTimeField(null=True, blank=True)
    completed = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=500, blank=True, null=True)
    
    def percent_complete(self):
        """Calculate transfer progress percentage."""
        if self.file_size > 0:
            return int((self.bytes_transferred / self.file_size) * 100)
        return 0