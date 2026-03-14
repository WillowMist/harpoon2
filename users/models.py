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


class Notification(models.Model):
    """User notifications for manual intervention required."""
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username}: {self.message[:50]}..."
    
    @classmethod
    def create_for_admin(cls, message):
        """Create a notification for the admin user."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        # Get the first superuser as admin
        admin = User.objects.filter(is_superuser=True).order_by('id').first()
        if admin:
            cls.objects.create(user=admin, message=message)
    
    @classmethod
    def get_unread_count(cls, user):
        """Get unread notification count for a user."""
        if user.is_authenticated and user.is_superuser:
            return cls.objects.filter(user=user, is_read=False).count()
        return 0