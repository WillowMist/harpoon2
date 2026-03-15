import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'harpoon2.settings')

import django
django.setup()

app = Celery('harpoon2')
# Load Django settings
from django.conf import settings
app.config_from_object(settings)

# Explicitly set the broker URL for Redis
app.conf.update(
    broker_url='redis://localhost:6379/0',
    result_backend='redis://localhost:6379/0',
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3300,  # 55 minute soft limit
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Set beat schedule
app.conf.beat_schedule = {
    # Poll managers every 20 seconds for newly grabbed items
    'poll-managers': {
        'task': 'entities.tasks.poll_managers',
        'schedule': 20.0,  # Every 20 seconds
    },
    # Poll Blackhole managers for new files
    'poll-blackhole-managers': {
        'task': 'entities.tasks.poll_blackhole_managers',
        'schedule': 20.0,  # Every 20 seconds
    },
    # Assign items to downloaders
    'assign-items': {
        'task': 'entities.tasks.assign_items_to_downloaders',
        'schedule': 20.0,  # Every 20 seconds
    },
    # Check downloaders every 20 seconds for completed items
    'check-downloaders': {
        'task': 'itemqueue.tasks.check_downloaders',
        'schedule': 20.0,  # Every 20 seconds
    },
    # Check for stalled transfers every 20 seconds
    'check-stalled-transfers': {
        'task': 'itemqueue.tasks.check_stalled_transfers',
        'schedule': 20.0,  # Every 20 seconds
    },
    # Check downloader for failures and notify manager
    'check-downloader-failures': {
        'task': 'itemqueue.tasks.check_downloader_failures',
        'schedule': 300.0,  # Every 5 minutes
    },
    # Cache downloader status for fast page loads
    'cache-downloader-status': {
        'task': 'entities.tasks.cache_downloader_status',
        'schedule': 10.0,  # Every 10 seconds
    },
}

# Also set as CELERY_BEAT_SCHEDULE for backwards compatibility
app.conf.CELERY_BEAT_SCHEDULE = app.conf.beat_schedule
