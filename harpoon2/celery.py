import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'harpoon2.settings')

app = Celery('harpoon2')
#app.config_from_object('harpoon2.settings')
app.config_from_object('django.conf:settings')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

app.conf.update(
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3300,  # 55 minute soft limit
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

app.conf.beat_schedule = {
    # Poll managers every minute for newly grabbed items
    'poll-managers': {
        'task': 'entities.tasks.poll_managers',
        'schedule': 60.0,  # Every 60 seconds
    },
    # Assign items to downloaders
    'assign-items': {
        'task': 'entities.tasks.assign_items_to_downloaders',
        'schedule': 60.0,  # Every 60 seconds
    },
    # Check downloaders every minute for completed items
    'check-downloaders': {
        'task': 'itemqueue.tasks.check_downloaders',
        'schedule': 60.0,  # Every 60 seconds
    },
    # Check for stalled transfers every minute
    'check-stalled-transfers': {
        'task': 'itemqueue.tasks.check_stalled_transfers',
        'schedule': 60.0,  # Every 60 seconds
    },
}
