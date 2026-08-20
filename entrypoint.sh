#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Harpoon2 Entrypoint ===${NC}"

# Ensure symlink exists - point to template so settings stay sync'd
if [ ! -L /opt/harpoon2/harpoon2/settings.py ] || [ ! -e /opt/harpoon2/harpoon2/settings.py ]; then
    echo -e "${YELLOW}Creating settings symlink to template...${NC}"
    rm -f /opt/harpoon2/harpoon2/settings.py
    ln -sf /opt/harpoon2/harpoon2/settings_template.py /opt/harpoon2/harpoon2/settings.py
fi

# Wait for Postgres to accept connections before running migrations.
# On a cold boot, docker-compose's depends_on + healthcheck can let
# the app container start before Postgres is actually ready at the
# protocol level, causing migrate/collectstatic to fail.
wait_for_postgres() {
    echo -e "${YELLOW}Waiting for PostgreSQL...${NC}"
    local host="${DB_HOST:-postgres}"
    local port="${DB_PORT:-5432}"
    local user="${DB_USER:-harpoon}"
    local pass="${DB_PASSWORD:-harpoon-default-password}"
    local db="${DB_NAME:-harpoon}"
    local max=60
    local i=0
    until python3 -c "
import os, psycopg2
try:
    c = psycopg2.connect(host='${host}', port=${port}, user='${user}', password='${pass}', dbname='${db}', connect_timeout=2)
    c.close()
except Exception as e:
    raise SystemExit(str(e))
" >/dev/null 2>&1; do
        i=$((i+1))
        if [ "$i" -ge "$max" ]; then
            echo -e "${RED}Postgres not reachable after ${max}s at ${host}:${port} - continuing anyway${NC}"
            return 1
        fi
        sleep 1
    done
    echo -e "${GREEN}PostgreSQL is ready${NC}"
}

# Wait for Redis to accept connections.
wait_for_redis() {
    echo -e "${YELLOW}Waiting for Redis...${NC}"
    local host="${REDIS_HOST:-redis}"
    local port="${REDIS_PORT:-6379}"
    local max=30
    local i=0
    until python3 -c "
import socket
s = socket.socket()
s.settimeout(2)
s.connect(('${host}', ${port}))
s.close()
" >/dev/null 2>&1; do
        i=$((i+1))
        if [ "$i" -ge "$max" ]; then
            echo -e "${RED}Redis not reachable after ${max}s at ${host}:${port} - continuing anyway${NC}"
            return 1
        fi
        sleep 1
    done
    echo -e "${GREEN}Redis is ready${NC}"
}

# Only run migrations and collect static for the 'start' command
# This prevents import errors when the settings aren't fully loaded yet

# Handle different commands
case "${1:-start}" in
    start)
        echo -e "${GREEN}Starting all services...${NC}"

        # Block until backing services are reachable - prevents race
        # on cold boot when Postgres/Redis healthchecks pass but the
        # app's first connection would otherwise fail.
        wait_for_postgres || true
        wait_for_redis || true

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

        # Start all services with Supervisor
        echo -e "${GREEN}Starting all services with Supervisor...${NC}"
        exec supervisord -c /opt/harpoon2/supervisord.conf
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
