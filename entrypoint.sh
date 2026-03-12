#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Harpoon2 Entrypoint ===${NC}"

# Check if /data/settings.py exists, if not create from template
if [ ! -f /data/settings.py ]; then
    echo -e "${YELLOW}Creating settings.py from template...${NC}"
    cp /opt/harpoon2/harpoon2/settings_template.py /data/settings.py 2>/dev/null || {
        echo -e "${YELLOW}Template not found, creating minimal settings.py...${NC}"
        cat > /data/settings.py << 'EOF'
import os
from pathlib import Path

# Note: settings.py is symlinked to /data/settings.py, so we need to use the app directory
BASE_DIR = Path('/opt/harpoon2')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,harpoon2').split(',')
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_celery_beat',
    'watson',
    'entities.apps.EntitiesConfig',
    'itemqueue.apps.ItemqueueConfig',
    'users.apps.UsersConfig',
    'crispy_forms',
    'crispy_bootstrap5',
    'crisp_modals',
]
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': '/data/harpoon2.db',
    }
}
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = os.environ.get('REDIS_PORT', '6379')
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
STATIC_URL = '/static/'
STATIC_ROOT = '/data/static/'
STATICFILES_DIRS = [
    Path('/opt/harpoon2') / 'static',
]
MEDIA_URL = '/media/'
MEDIA_ROOT = '/data/media/'
USE_TZ = True
TIME_ZONE = 'UTC'
INTERFACE = 'vapor'
THEMES = [('cerulean', 'Cerulean'),
          ('cosmo', 'Cosmo'),
          ('cyborg', 'Cyborg'),
          ('darkly', 'Darkly'),
          ('flatly', 'Flatly'),
          ('journal', 'Journal'),
          ('litera', 'Litera'),
          ('lumen', 'Lumen'),
          ('lux', 'Lux'),
          ('materia', 'Materia'),
          ('minty', 'Minty'),
          ('morph', 'Morph'),
          ('pulse', 'Pulse'),
          ('quartz', 'Quartz'),
          ('sandstone', 'Sandstone'),
          ('simplex', 'Simplex'),
          ('sketchy', 'Sketchy'),
          ('slate', 'Slate'),
          ('solar', 'Solar'),
          ('spacelab', 'Spacelab'),
          ('superhero', 'Superhero'),
          ('united', 'United'),
          ('vapor', 'Vapor'),
          ('yeti', 'Yeti'),
          ('zephyr', 'Zephyr')]
MANAGER_TYPES = [
    ('Sonarr', 'Sonarr'),
    ('Radarr', 'Radarr'),
    ('Lidarr', 'Lidarr'),
    ('Readarr', 'Readarr'),
    ('Whisparr', 'Whisparr'),
    ('LazyLibrarian', 'LazyLibrarian'),
    ('Mylar', 'Mylar'),
]
DOWNLOADER_TYPES = [
    ('RTorrent', 'RTorrent'),
    ('SABNzbd', 'SABNzbd'),
]
EOF
    }
    chmod 644 /data/settings.py
fi

# Ensure symlink exists
if [ ! -L /opt/harpoon2/harpoon2/settings.py ] || [ ! -e /opt/harpoon2/harpoon2/settings.py ]; then
    echo -e "${YELLOW}Creating settings symlink...${NC}"
    rm -f /opt/harpoon2/harpoon2/settings.py
    ln -sf /data/settings.py /opt/harpoon2/harpoon2/settings.py
fi

# Only run migrations and collect static for the 'start' command
# This prevents import errors when the settings aren't fully loaded yet

# Handle different commands
case "${1:-start}" in
    start)
        echo -e "${GREEN}Starting all services...${NC}"
        
        # Run migrations (only on first startup or when needed)
        echo -e "${YELLOW}Checking database...${NC}"
        if [ ! -f /data/harpoon2.db ]; then
            echo -e "${YELLOW}Running initial database migrations...${NC}"
            python3 manage.py migrate --noinput || true
            
            echo -e "${YELLOW}Collecting static files...${NC}"
            python3 manage.py collectstatic --noinput --clear || true
        fi
        
        # Start Redis
        echo -e "${YELLOW}Starting Redis server...${NC}"
        redis-server --daemonize yes --logfile /var/log/harpoon2/redis.log
        sleep 2
        
        # Start Celery Beat
        echo -e "${YELLOW}Starting Celery Beat scheduler...${NC}"
        celery -A harpoon2 beat -l info --logfile=/var/log/harpoon2/celery-beat.log &
        BEAT_PID=$!
        
        # Start Celery Worker
        echo -e "${YELLOW}Starting Celery worker...${NC}"
        celery -A harpoon2 worker -l info --logfile=/var/log/harpoon2/celery-worker.log &
        WORKER_PID=$!
        
        # Start Django development server
        echo -e "${GREEN}Starting Django development server on 0.0.0.0:8000${NC}"
        python3 manage.py runserver 0.0.0.0:8000
        
        # Trap signals to gracefully shutdown
        trap "kill $BEAT_PID $WORKER_PID; exit" SIGTERM SIGINT
        wait
        ;;
        
    django)
        echo -e "${GREEN}Starting Django only...${NC}"
        python3 manage.py runserver 0.0.0.0:8000
        ;;
        
    worker)
        echo -e "${GREEN}Starting Celery worker only...${NC}"
        celery -A harpoon2 worker -l info
        ;;
        
    beat)
        echo -e "${GREEN}Starting Celery beat only...${NC}"
        celery -A harpoon2 beat -l info
        ;;
        
    redis)
        echo -e "${GREEN}Starting Redis only...${NC}"
        redis-server --bind 0.0.0.0 --logfile ""
        ;;
        
    migrate)
        echo -e "${GREEN}Running migrations...${NC}"
        python3 manage.py migrate
        ;;
        
    createsuperuser)
        echo -e "${GREEN}Creating superuser...${NC}"
        python3 manage.py createsuperuser
        ;;
        
    shell)
        echo -e "${GREEN}Starting Django shell...${NC}"
        python3 manage.py shell
        ;;
        
    bash)
        echo -e "${GREEN}Starting bash shell...${NC}"
        /bin/bash
        ;;
        
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        echo "Available commands:"
        echo "  start          - Start all services (default)"
        echo "  django         - Start Django only"
        echo "  worker         - Start Celery worker only"
        echo "  beat           - Start Celery beat scheduler only"
        echo "  redis          - Start Redis only"
        echo "  migrate        - Run database migrations"
        echo "  createsuperuser - Create superuser"
        echo "  shell          - Start Django shell"
        echo "  bash           - Start bash shell"
        exit 1
        ;;
esac
