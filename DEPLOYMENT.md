# Harpoon v2 Deployment Guide

Complete setup instructions for deploying Harpoon v2 with Nginx, Gunicorn (WSGI), Celery, and Redis.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Database Setup](#database-setup)
4. [Configuration](#configuration)
5. [Running Services](#running-services)
6. [Systemd Services](#systemd-services)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

- Ubuntu 20.04+ or similar Linux distribution
- Python 3.10+
- Redis server
- Nginx
- Git

## Installation

### 1. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv nginx redis-server git supervisor
```

### 2. Clone Repository

```bash
git clone <repository-url> /opt/harpoon2
cd /opt/harpoon2
```

### 3. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Install Python Dependencies

```bash
pip install -r requirements.txt
pip install gunicorn
```

## Database Setup

### 1. Apply Migrations

```bash
source venv/bin/activate
cd /opt/harpoon2
python manage.py migrate
```

### 2. Create Superuser (Optional)

```bash
python manage.py createsuperuser
```

### 3. Collect Static Files

```bash
python manage.py collectstatic --noinput
```

## Configuration

### 1. Create `.env` file (if using environment variables)

```bash
cat > /opt/harpoon2/.env << 'EOF'
DEBUG=False
ALLOWED_HOSTS=your-domain.com,www.your-domain.com,your-ip-address
SECRET_KEY=your-long-secret-key-here
DATABASE_URL=sqlite:////opt/harpoon2/db.sqlite3
REDIS_URL=redis://localhost:6379/0
EOF
```

### 2. Update Django Settings (harpoon2/settings.py)

Ensure the following settings are configured:

```python
# Production settings
DEBUG = False
ALLOWED_HOSTS = ['your-domain.com', 'www.your-domain.com', 'your-ip']

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = '/opt/harpoon2/static/'

# Redis/Celery
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
REDIS_HOST = 'localhost'
REDIS_PORT = '6379'
BROKER_URL = 'redis://localhost:6379/0'

# Security
CSRF_TRUSTED_ORIGINS = ['https://your-domain.com']
```

### 3. Nginx Configuration

Create `/etc/nginx/sites-available/harpoon2`:

```nginx
upstream harpoon2_wsgi {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name your-domain.com www.your-domain.com;
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com www.your-domain.com;
    
    # SSL certificates (use Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    client_max_body_size 100M;
    
    location /static/ {
        alias /opt/harpoon2/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    location / {
        proxy_pass http://harpoon2_wsgi;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        
        # Timeouts for long operations
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/harpoon2 /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Running Services

### 1. Gunicorn (WSGI Application Server)

Basic command:

```bash
cd /opt/harpoon2
source venv/bin/activate
gunicorn harpoon2.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 4 \
    --worker-class sync \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
```

### 2. Celery Worker

```bash
cd /opt/harpoon2
source venv/bin/activate
celery -A harpoon2 worker -l info
```

### 3. Celery Beat (Scheduler)

```bash
cd /opt/harpoon2
source venv/bin/activate
celery -A harpoon2 beat -l info
```

### 4. Redis Server

```bash
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

## Systemd Services

Create systemd service files for automatic startup and management.

### 1. Gunicorn Service: `/etc/systemd/system/harpoon2-gunicorn.service`

```ini
[Unit]
Description=Harpoon2 Gunicorn Application Server
After=network.target redis-server.service

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/opt/harpoon2
Environment="PATH=/opt/harpoon2/venv/bin"
Environment="DJANGO_SETTINGS_MODULE=harpoon2.settings"
ExecStart=/opt/harpoon2/venv/bin/gunicorn \
    --workers 4 \
    --worker-class sync \
    --bind 127.0.0.1:8000 \
    --timeout 120 \
    --access-logfile /var/log/harpoon2/gunicorn-access.log \
    --error-logfile /var/log/harpoon2/gunicorn-error.log \
    harpoon2.wsgi:application

Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 2. Celery Worker Service: `/etc/systemd/system/harpoon2-celery.service`

```ini
[Unit]
Description=Harpoon2 Celery Worker
After=network.target redis-server.service

[Service]
Type=forking
User=www-data
Group=www-data
WorkingDirectory=/opt/harpoon2
Environment="PATH=/opt/harpoon2/venv/bin"
Environment="DJANGO_SETTINGS_MODULE=harpoon2.settings"
ExecStart=/opt/harpoon2/venv/bin/celery -A harpoon2 worker \
    --loglevel=info \
    --logfile=/var/log/harpoon2/celery-worker.log \
    --pidfile=/var/run/harpoon2-celery.pid

Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 3. Celery Beat Service: `/etc/systemd/system/harpoon2-celery-beat.service`

```ini
[Unit]
Description=Harpoon2 Celery Beat Scheduler
After=network.target redis-server.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/harpoon2
Environment="PATH=/opt/harpoon2/venv/bin"
Environment="DJANGO_SETTINGS_MODULE=harpoon2.settings"
ExecStart=/opt/harpoon2/venv/bin/celery -A harpoon2 beat \
    --loglevel=info \
    --logfile=/var/log/harpoon2/celery-beat.log \
    --scheduler django_celery_beat.schedulers:DatabaseScheduler

Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 4. Create Log Directory

```bash
sudo mkdir -p /var/log/harpoon2
sudo chown www-data:www-data /var/log/harpoon2
sudo chmod 755 /var/log/harpoon2
```

### 5. Enable and Start Services

```bash
# Reload systemd daemon
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable harpoon2-gunicorn.service
sudo systemctl enable harpoon2-celery.service
sudo systemctl enable harpoon2-celery-beat.service
sudo systemctl enable redis-server

# Start all services
sudo systemctl start redis-server
sudo systemctl start harpoon2-gunicorn.service
sudo systemctl start harpoon2-celery.service
sudo systemctl start harpoon2-celery-beat.service
```

## Verification

### Check Service Status

```bash
sudo systemctl status harpoon2-gunicorn.service
sudo systemctl status harpoon2-celery.service
sudo systemctl status harpoon2-celery-beat.service
sudo systemctl status redis-server
```

### Check Logs

```bash
# Gunicorn logs
sudo tail -f /var/log/harpoon2/gunicorn-error.log

# Celery worker logs
sudo tail -f /var/log/harpoon2/celery-worker.log

# Celery beat logs
sudo tail -f /var/log/harpoon2/celery-beat.log
```

### Verify Celery Tasks

```bash
cd /opt/harpoon2
source venv/bin/activate
celery -A harpoon2 inspect active
```

## SSL/TLS with Let's Encrypt

### Install Certbot

```bash
sudo apt-get install -y certbot python3-certbot-nginx
```

### Generate Certificate

```bash
sudo certbot certonly --nginx -d your-domain.com -d www.your-domain.com
```

### Auto-renewal

Certbot automatically sets up a cron job for renewal. Verify:

```bash
sudo systemctl status certbot.timer
sudo systemctl enable certbot.timer
```

## Troubleshooting

### Services Won't Start

1. Check logs for errors:
```bash
sudo journalctl -u harpoon2-gunicorn -n 50
sudo journalctl -u harpoon2-celery -n 50
```

2. Verify permissions:
```bash
sudo chown -R www-data:www-data /opt/harpoon2
sudo chmod -R 755 /opt/harpoon2
```

### Redis Connection Issues

```bash
# Test Redis connection
redis-cli ping
# Should return: PONG

# Check Redis is running
sudo systemctl status redis-server

# Restart Redis
sudo systemctl restart redis-server
```

### Database Lock Issues

```bash
# Remove database lock (if crashed)
rm -f /opt/harpoon2/db.sqlite3-journal
```

### Celery Tasks Not Running

1. Check beat schedule:
```bash
cd /opt/harpoon2
source venv/bin/activate
python manage.py shell
from django_celery_beat.models import PeriodicTask
PeriodicTask.objects.all().values()
```

2. Restart beat scheduler:
```bash
sudo systemctl restart harpoon2-celery-beat.service
```

### High Memory Usage

Reduce Gunicorn workers:

```bash
# Edit service file
sudo nano /etc/systemd/system/harpoon2-gunicorn.service

# Change --workers from 4 to 2 or 1
# Then reload:
sudo systemctl daemon-reload
sudo systemctl restart harpoon2-gunicorn.service
```

## Monitoring

### Install Monitoring Tools (Optional)

```bash
sudo apt-get install -y htop iotop nethogs
```

### Monitor Processes

```bash
# Monitor system resources
htop

# Monitor I/O
sudo iotop

# Monitor network
sudo nethogs
```

## Backup

### Database Backup

```bash
# Backup SQLite database
cp /opt/harpoon2/db.sqlite3 /backup/harpoon2-$(date +%Y%m%d).sqlite3

# Or automate with cron
0 2 * * * cp /opt/harpoon2/db.sqlite3 /backup/harpoon2-$(date +\%Y\%m\%d).sqlite3
```

## Performance Tuning

### Increase Gunicorn Workers

For servers with more CPU cores, increase workers:

```ini
ExecStart=/opt/harpoon2/venv/bin/gunicorn \
    --workers 8 \  # Increase based on CPU cores
    ...
```

### Celery Concurrency

For better performance:

```ini
ExecStart=/opt/harpoon2/venv/bin/celery -A harpoon2 worker \
    --concurrency=4 \
    ...
```

### Redis Persistence

Edit `/etc/redis/redis.conf`:

```conf
# Enable persistence
save 900 1      # Save if 1 key changed in 900 seconds
save 300 10     # Save if 10 keys changed in 300 seconds
save 60 10000   # Save if 10000 keys changed in 60 seconds

appendonly yes  # Enable append-only file
```

## Updating

To update Harpoon v2:

```bash
cd /opt/harpoon2
git pull origin master
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart harpoon2-gunicorn.service
```

## Support & Debugging

For issues or questions:
- Check logs in `/var/log/harpoon2/`
- Run `sudo systemctl status` for each service
- Check Redis connectivity: `redis-cli ping`
- Verify database: `python manage.py migrate --check`
