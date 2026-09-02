import os
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'harpoon2.settings')

import django
django.setup()

app = Celery('harpoon2')
# Load Django settings
from django.conf import settings
app.config_from_object(settings)

# Ensure broker and result backend are set from environment variables
app.conf.update(
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=settings.CELERY_RESULT_BACKEND,
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3300,  # 55 minute soft limit
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Dedicated queue for the self-healing watchdog. The watchdog must run on its
# own worker pool so it always has a slot even when the main pool is saturated
# with hung transfer tasks — otherwise it sits in the queue behind those tasks
# and never executes (a smoke alarm with no sprinkler). Normal tasks stay on
# the default 'celery' queue; only itemqueue.tasks.celery_watchdog is routed
# to 'celery.watchdog'. The beat schedule entry publishes to the routed queue.
app.conf.task_queues = (
    Queue('celery', routing_key='celery'),
    Queue('celery.watchdog', routing_key='celery.watchdog'),
)
app.conf.task_default_queue = 'celery'
app.conf.task_routes = {
    'itemqueue.tasks.celery_watchdog': {'queue': 'celery.watchdog'},
}

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
    # Clean up expired sessions daily
    'cleanup-sessions': {
        'task': 'harpoon2.tasks.cleanup_sessions',
        'schedule': crontab(hour=3, minute=0),  # Daily at 3am
    },
    # Self-healing watchdog — flushes Redis queue when wedged and logs
    # tasks that exceed the safety age threshold. Runs every 60s; bounded
    # by its own 60s time_limit.
    'celery-watchdog': {
        'task': 'itemqueue.tasks.celery_watchdog',
        'schedule': 60.0,
    },
    # AirDC++ completion check — every 5 minutes, SFTP-walks AirDC++ share for
    # Items whose timer is due (set by Mylar3.poll() and poll_managers() when an
    # AirDC++ download is first attempted).
    'check-airdcpp-completions': {
        'task': 'itemqueue.tasks.check_airdcpp_completions',
        'schedule': 300.0,
    },
}

# Also set as CELERY_BEAT_SCHEDULE for backwards compatibility
app.conf.CELERY_BEAT_SCHEDULE = app.conf.beat_schedule
