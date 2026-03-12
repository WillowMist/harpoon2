# Harpoon2 Docker Setup Guide

This guide explains how to run Harpoon2 in Docker containers.

## Prerequisites

- Docker and Docker Compose installed
- 2GB+ RAM available
- 5GB+ disk space

## Quick Start

### 1. Build and Start

```bash
docker-compose up -d
```

This will:
- Build the Harpoon2 image
- Start Redis server
- Initialize database
- Run Django development server on port 8000
- Start Celery worker and beat scheduler

### 2. Access the Application

Open your browser to: `http://localhost:8000`

### 3. Create Superuser (First Time Only)

```bash
docker-compose exec harpoon2 createsuperuser
```

## Directory Structure

```
/data                    # Persistent data volume
├── settings.py         # Django settings (auto-created)
├── harpoon2.db         # SQLite database
├── static/             # Collected static files
└── media/              # Media files
```

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
SECRET_KEY=your-secret-key-here
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1,your-domain.com
```

## Common Commands

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f harpoon2
docker-compose logs -f redis
```

### Access Django Shell

```bash
docker-compose exec harpoon2 shell
```

### Run Migrations Manually

```bash
docker-compose exec harpoon2 migrate
```

### Start Individual Services

```bash
# Django only
docker-compose exec harpoon2 django

# Celery worker only
docker-compose exec harpoon2 worker

# Celery beat only
docker-compose exec harpoon2 beat

# Redis only
docker-compose exec harpoon2 redis

# Bash shell
docker-compose exec harpoon2 bash
```

### Collect Static Files

```bash
docker-compose exec harpoon2 python manage.py collectstatic --noinput
```

### Create Backup

```bash
# Backup database and config
docker run --rm -v harpoon2_harpoon2-data:/data -v $(pwd):/backup \
  ubuntu tar czf /backup/harpoon2-backup.tar.gz -C /data .
```

### Restore from Backup

```bash
# Restore database and config
docker run --rm -v harpoon2_harpoon2-data:/data -v $(pwd):/backup \
  ubuntu tar xzf /backup/harpoon2-backup.tar.gz -C /data
```

## Services

### Harpoon2 (Main Application)
- **Port**: 8000
- **Command**: `start` (runs Django, Celery worker, and Celery beat)
- **Volumes**: 
  - `/data` - Persistent data (database, settings, media, static files)
  - Current directory mounted to `/opt/harpoon2` for development

### Redis
- **Port**: 6379
- **Role**: Celery message broker
- **Data**: Stored in `harpoon2-redis-data` volume

## Production Deployment

For production, use a proper WSGI server and reverse proxy:

### 1. Update Dockerfile for Production

```dockerfile
# Change DEBUG to False
# Use gunicorn instead of development server
RUN pip install gunicorn
```

### 2. Use Gunicorn

```bash
docker-compose exec harpoon2 gunicorn harpoon2.wsgi:application --bind 0.0.0.0:8000
```

### 3. Add Nginx Reverse Proxy

Uncomment the nginx service in `docker-compose.yml` and configure:

```nginx
upstream harpoon2 {
    server harpoon2:8000;
}

server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://harpoon2;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /static/ {
        alias /data/static/;
    }
}
```

### 4. Enable HTTPS with Let's Encrypt

Use Certbot with Nginx for SSL certificates.

## Troubleshooting

### Port Already in Use

```bash
# Change port in docker-compose.yml
# Or free up the port
lsof -i :8000  # Find process using port
kill -9 <PID>
```

### Database Migrations Failed

```bash
# Check database integrity
docker-compose exec harpoon2 python manage.py showmigrations

# Apply pending migrations
docker-compose exec harpoon2 migrate
```

### Redis Connection Issues

```bash
# Check Redis health
docker-compose exec redis redis-cli ping
# Should return: PONG
```

### Celery Tasks Not Running

```bash
# Check Celery worker logs
docker-compose logs -f harpoon2

# Verify Redis broker connectivity
docker-compose exec redis redis-cli
> KEYS *
```

### Out of Memory

```bash
# Increase Docker memory allocation
# Edit docker-compose.yml and add:
# mem_limit: 2g
```

## Volumes

### harpoon2-data
Stores persistent application data:
- SQLite database
- Settings file
- Static files
- Media files

### harpoon2-redis-data
Stores Redis persistence data

To list volumes:
```bash
docker volume ls | grep harpoon2
```

To inspect volume:
```bash
docker volume inspect harpoon2_harpoon2-data
```

## Cleaning Up

### Remove Stopped Containers

```bash
docker-compose down
```

### Remove All Data (Destructive!)

```bash
docker-compose down -v  # Removes volumes too
```

## Performance Tuning

### Increase Celery Concurrency

Edit docker-compose.yml:

```bash
command: start  # Add concurrency flag
# celery -A harpoon2 worker -l info --concurrency=4
```

### Enable Redis Persistence

Redis is configured to save data. For more control, edit the Redis configuration in docker-compose.yml.

## Monitoring

### Check Container Status

```bash
docker-compose ps
```

### Monitor Resource Usage

```bash
docker stats harpoon2 redis
```

### Check Logs for Errors

```bash
docker-compose logs --tail=100 harpoon2
```

## Further Documentation

- Django: https://docs.djangoproject.com/
- Celery: https://docs.celeryproject.org/
- Docker: https://docs.docker.com/
