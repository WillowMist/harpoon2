from django.db import models
from django.db.models import Q

# Create your models here.

class Item(models.Model):
    name = models.CharField(max_length=500, default='', blank=True)
    hash = models.CharField(max_length=200, primary_key=True)
    manager = models.ForeignKey('entities.Manager', on_delete=models.CASCADE, null=True, blank=True)
    downloader = models.ForeignKey('entities.Downloader', on_delete=models.CASCADE, null=True, blank=True)
    size = models.BigIntegerField(default=0)
    received = models.BigIntegerField(default=0)
    status = models.CharField(max_length=50, default='Created', db_index=True)
    manager_ref_id = models.BigIntegerField(null=True, blank=True)
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

    # Phase 5 recovery-loop cooldown. NULL means "cooldown elapsed" — no
    # backfill needed. db_index=True keeps the check_stalled query
    # (Q(last_recovery_at__isnull=True) | Q(last_recovery_at__lt=now-60s))
    # indexed.
    last_recovery_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text='Last time the recovery loop requeued this item; NULL means cooldown elapsed.')

    # PIPE-01: retry_postprocessing attempt counter. Default 0; the count is
    # bumped inside the task before apply_async so a worker restart doesn't
    # reset it. Hard cap = 3 (RETRY_CAP_ATTEMPTS in itemqueue/tasks.py).
    attempt_count = models.IntegerField(default=0, help_text='retry_postprocessing attempts; PIPE-01 hard cap = 3.')

    # AirDC++ completion-check timer. Set by Mylar3.poll() / poll_managers() when
    # an Item is created with AirDC++ as the downloader. The check_airdcpp_completions
    # beat task picks up Items where next_check_at <= now() and SFTP-walks AirDC++ for
    # the expected path.
    next_check_at = models.DateTimeField(null=True, blank=True, db_index=True,
                                          help_text='AirDC++ SFTP check time (nullable).')
    airdcpp_expected_path = models.CharField(max_length=500, null=True, blank=True,
                                              help_text='Expected file or folder name on AirDC++ share (nullable).')
    airdcpp_check_count = models.IntegerField(default=0,
                                                help_text='Number of AirDC++ completion checks performed (resets on success).')

    class Meta:
        indexes = [
            models.Index(fields=['status', 'archived', '-modified']),
            models.Index(fields=['status', 'archived', '-archived_at']),
        ]


class ItemHistory(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='history')
    created = models.DateTimeField(auto_now_add=True)
    details = models.CharField(max_length=500)


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

    class Meta:
        constraints = [
            # DB-level uniqueness on (item, filename). Required to make
            # get_or_create() actually atomic — without this, two concurrent
            # transfer_files_async tasks (e.g., check_downloaders + Block B
            # requeue) can both INSERT the same (item, filename) and both
            # succeed, leaving duplicate rows that inflate the dashboard's
            # sum(file_size) and cause the same file to be copied N times.
            models.UniqueConstraint(
                fields=['item', 'filename'],
                name='uniq_itemqueue_filetransfer_item_filename',
            ),
        ]