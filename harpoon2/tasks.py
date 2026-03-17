from celery import shared_task
from django.contrib.sessions.models import Session
from django.utils import timezone


@shared_task
def cleanup_sessions():
    """Delete expired sessions to keep database small."""
    expired = Session.objects.filter(expire_date__lt=timezone.now())
    count = expired.count()
    expired.delete()
    return f"Deleted {count} expired sessions"
