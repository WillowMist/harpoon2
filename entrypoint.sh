#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Harpoon2 Entrypoint ===${NC}"

# Ensure symlink exists - point to template so settings stay in sync
if [ ! -L /opt/harpoon2/harpoon2/settings.py ] || [ ! -e /opt/harpoon2/harpoon2/settings.py ]; then
    echo -e "${YELLOW}Creating settings symlink to template...${NC}"
    rm -f /opt/harpoon2/harpoon2/settings.py
    ln -sf /opt/harpoon2/harpoon2/settings_template.py /opt/harpoon2/harpoon2/settings.py
fi

# Only run migrations and collect static for the 'start' command
# This prevents import errors when the settings aren't fully loaded yet

# Handle different commands
case "${1:-start}" in
    start)
        echo -e "${GREEN}Starting all services...${NC}"
        
        # Create any missing migrations
        echo -e "${YELLOW}Creating database migrations...${NC}"
        python3 manage.py makemigrations --noinput || true
        
        # Run migrations on every startup (Django is smart enough to only apply new ones)
        echo -e "${YELLOW}Checking database migrations...${NC}"
        python3 manage.py migrate --noinput || true
        
        # Install Watson search index
        echo -e "${YELLOW}Updating search index...${NC}"
        python3 manage.py installwatson --verbosity=1 || true
        
        # Collect static files if needed
        echo -e "${YELLOW}Collecting static files...${NC}"
        python3 manage.py collectstatic --noinput --clear || true
        
        # Start Redis
        echo -e "${YELLOW}Starting Redis server...${NC}"
        redis-server --daemonize yes --logfile /var/log/harpoon2/redis.log
        sleep 2
        
        # Start Celery Beat in background
        echo -e "${YELLOW}Starting Celery Beat scheduler...${NC}"
        (celery -A harpoon2 beat -l info --schedule=/data/celerybeat-schedule --logfile=/var/log/harpoon2/celery-beat.log &)
        sleep 1
        
        # Start Celery Worker in background  
        echo -e "${YELLOW}Starting Celery worker...${NC}"
        (celery -A harpoon2 worker -l debug --logfile=/var/log/harpoon2/celery-worker.log &)
        sleep 1
        
        # Start Django development server in foreground
        echo -e "${GREEN}Starting Django development server on 0.0.0.0:4277${NC}"
        exec python3 manage.py runserver 0.0.0.0:4277
        ;;
        
    django)
        echo -e "${GREEN}Starting Django only...${NC}"
        python3 manage.py runserver 0.0.0.0:4277
        ;;
        
    worker)
        echo -e "${GREEN}Starting Celery worker only...${NC}"
        celery -A harpoon2 worker -l debug
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
