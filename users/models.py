from django.contrib.auth.models import AbstractUser
from django.db import models
from harpoon2.settings import THEMES
from django.urls import reverse
from dplibs.session import clear_inactive_sessions, get_sessions
import pytz

def timezone_choices():
    choices = []
    for zone in pytz.common_timezones:
        choices.append((zone, zone))
    return choices


class CustomUser(AbstractUser):
    interface = models.CharField(max_length=20, blank=True, choices=THEMES)
    prefs = models.CharField(max_length=200, blank=True)
    timezone = models.CharField(max_length=40, blank=True, choices=timezone_choices())

    # add additional fields in here

    def __str__(self):
        return self.username

    def get_active_sessions(self):
        clear_inactive_sessions()
        user_sessions = get_sessions(userid=self.id)
        return user_sessions

    def get_active_session_count(self):
        return len(self.get_active_sessions())

    def get_absolute_url(self):
        return reverse('users:detail', kwargs={'userid': self.pk})

    def get_link(self):
        return f'<a href={self.get_absolute_url()}>{self.username}</a>'


class NotificationSettings(models.Model):
    """Settings for which notification types to receive."""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='notification_settings')
    
    # Downloader notifications
    notify_downloader_failure = models.BooleanField(default=True)
    notify_torrent_not_found = models.BooleanField(default=True)
    notify_torrent_incomplete = models.BooleanField(default=True)
    notify_sabnzbd_not_found = models.BooleanField(default=True)
    notify_sabnzbd_incomplete = models.BooleanField(default=True)
    notify_transfer_not_found = models.BooleanField(default=True)
    
    # Transfer notifications
    notify_transfer_failure = models.BooleanField(default=True)
    notify_sftp_failure = models.BooleanField(default=True)
    
    # Extraction notifications
    notify_zip_failure = models.BooleanField(default=True)
    notify_rar_failure = models.BooleanField(default=True)
    
    # Post-processing notifications
    notify_postprocess_failure = models.BooleanField(default=True)
    notify_manual_intervention = models.BooleanField(default=True)
    
    # Queue notifications
    notify_item_completed = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Notification settings for {self.user.username}"
    
    @classmethod
    def get_for_user(cls, user):
        """Get or create notification settings for a user."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj


class Notification(models.Model):
    """User notifications for manual intervention required."""
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    notification_type = models.CharField(max_length=50, blank=True)  # e.g., 'downloader_failure', 'transfer_failure'
    item_hash = models.CharField(max_length=64, blank=True)  # Link to item if applicable
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username}: {self.message[:50]}..."
    
    @classmethod
    def create_for_admin(cls, message, notification_type='', item_hash=''):
        """Create a notification for the admin user if notifications are enabled."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).order_by('id').first()
        
        if not admin:
            return None
        
        # Check if notifications are enabled for this type
        settings = NotificationSettings.get_for_user(admin)
        should_notify = cls._should_notify(notification_type, settings)
        
        if should_notify:
            return cls.objects.create(
                user=admin, 
                message=message,
                notification_type=notification_type,
                item_hash=item_hash
            )
        return None
    
    @classmethod
    def _should_notify(cls, notification_type, settings):
        """Check if notification is enabled for the given type."""
        if not notification_type:
            return True
            
        type_map = {
            'downloader_failure': settings.notify_downloader_failure,
            'torrent_not_found': settings.notify_torrent_not_found,
            'torrent_incomplete': settings.notify_torrent_incomplete,
            'sabnzbd_not_found': settings.notify_sabnzbd_not_found,
            'sabnzbd_incomplete': settings.notify_sabnzbd_incomplete,
            'transfer_not_found': settings.notify_transfer_not_found,
            'transfer_failure': settings.notify_transfer_failure,
            'sftp_failure': settings.notify_sftp_failure,
            'zip_failure': settings.notify_zip_failure,
            'rar_failure': settings.notify_rar_failure,
            'postprocess_failure': settings.notify_postprocess_failure,
            'manual_intervention': settings.notify_manual_intervention,
            'item_completed': settings.notify_item_completed,
        }
        return type_map.get(notification_type, True)
    
    @classmethod
    def get_unread_count(cls, user):
        """Get unread notification count for a user."""
        if user.is_authenticated and user.is_superuser:
            return cls.objects.filter(user=user, is_read=False).count()
        return 0