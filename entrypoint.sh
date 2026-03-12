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

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_extensions',
    'crispy_forms',
    'crispy_bootstrap5',
    'watson',
    'django_celery_beat',
    'django_celery_results',
    'users',
    'entities',
    'itemqueue',
]
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': '/data/harpoon2.db',
    }
}
CELERY_BROKER_URL = 'redis://redis:6379/0'
CELERY_RESULT_BACKEND = 'redis://redis:6379/0'
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
STATIC_URL = '/static/'
STATIC_ROOT = '/data/static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = '/data/media/'
USE_TZ = True
TIME_ZONE = 'UTC'
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

# Run database migrations
echo -e "${YELLOW}Running database migrations...${NC}"
python manage.py migrate --noinput

# Collect static files
echo -e "${YELLOW}Collecting static files...${NC}"
python manage.py collectstatic --noinput --clear

# Handle different commands
case "${1:-start}" in
    start)
        echo -e "${GREEN}Starting all services...${NC}"
        
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
        python manage.py runserver 0.0.0.0:8000
        
        # Trap signals to gracefully shutdown
        trap "kill $BEAT_PID $WORKER_PID; exit" SIGTERM SIGINT
        wait
        ;;
        
    django)
        echo -e "${GREEN}Starting Django only...${NC}"
        python manage.py runserver 0.0.0.0:8000
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
        python manage.py migrate
        ;;
        
    createsuperuser)
        echo -e "${GREEN}Creating superuser...${NC}"
        python manage.py createsuperuser
        ;;
        
    shell)
        echo -e "${GREEN}Starting Django shell...${NC}"
        python manage.py shell
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
