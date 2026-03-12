# Harpoon v2 Quick Start Guide

Get Harpoon v2 up and running quickly for development or production.

## Development Setup (5 minutes)

### 1. Clone and Setup

```bash
git clone <repo-url> harpoon2
cd harpoon2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
python manage.py migrate
python manage.py createsuperuser  # Create admin user
```

### 3. Run Development Server

Terminal 1 - Django:
```bash
source venv/bin/activate
python manage.py runserver 0.0.0.0:8000
```

Terminal 2 - Celery Worker:
```bash
source venv/bin/activate
celery -A harpoon2 worker -l info
```

Terminal 3 - Celery Beat (Scheduler):
```bash
source venv/bin/activate
celery -A harpoon2 beat -l info
```

Terminal 4 - Redis:
```bash
redis-server
```

Access at: `http://localhost:8000`

## Configuration

### 1. Configure Managers (Sonarr/Radarr/Lidarr/Whisparr)

Visit `http://localhost:8000/entities/managers/`

- Add your manager URLs and API keys
- Configure download folders

### 2. Configure Downloaders (RTorrent/SABnzbd)

Visit `http://localhost:8000/entities/downloaders/`

- Add RTorrent or SABnzbd connection info

### 3. Configure Seedbox (SFTP)

Visit `http://localhost:8000/entities/seedboxes/`

- Add seedbox credentials
- SSH key or password authentication
- Specify remote download path

### 4. Configure Local Download Folders

Visit `http://localhost:8000/entities/downloadfolders/`

- Specify local paths for each manager category
- Where files will be transferred to

## Key Features

### Dashboard
- View active downloads and transfers
- Real-time transfer progress
- Manager statistics
- Quick access to queue and history

### Queue
- All grabbed items awaiting transfer
- Current downloads and transfers
- Cancel individual downloads
- Expandable item details

### History
- Completed items
- Failed items
- **Toggle to view archived items**
- Archive/unarchive individual items
- Bulk archive failed or completed items

### File Transfer
- SFTP transfer with verification
- Real-time progress monitoring
- Single-file torrent optimization
- Auto-extraction support
- Stalled transfer detection

## Workflow

1. **Grab**: Managers (Sonarr/Radarr/Lidarr/Whisparr) find and grab items
2. **Download**: RTorrent or SABnzbd downloads the item
3. **Detect**: Harpoon detects completion via polling
4. **Transfer**: Files transferred via SFTP to local storage
5. **Complete**: Item marked as completed and displayed in history

## Admin Interface

Access Django admin at: `http://localhost:8000/admin`

- Manage users and permissions
- View database records
- Troubleshoot issues

## Common Commands

### Database
```bash
python manage.py migrate              # Apply migrations
python manage.py makemigrations       # Create new migrations
python manage.py shell                # Django shell
python manage.py createsuperuser      # Create admin user
```

### Celery
```bash
celery -A harpoon2 worker -l info     # Start worker
celery -A harpoon2 beat -l info       # Start beat scheduler
celery -A harpoon2 inspect active     # Check active tasks
```

### Utilities
```bash
python manage.py runserver            # Development server
python manage.py collectstatic        # Collect static files
gunicorn harpoon2.wsgi:application    # Production server
```

## Troubleshooting

### Redis Connection Error
```bash
# Check Redis is running
redis-cli ping  # Should return PONG

# Start Redis if not running
redis-server
```

### Celery Tasks Not Running
```bash
# Check beat schedule
python manage.py shell
from django_celery_beat.models import PeriodicTask
PeriodicTask.objects.all().values()

# Restart beat
celery -A harpoon2 beat -l info
```

### Database Issues
```bash
# Remove lock file if crashed
rm -f db.sqlite3-journal

# Reapply migrations
python manage.py migrate --fake-initial
```

### Import Errors
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

## Production Deployment

For full production setup with Nginx, Gunicorn, and Systemd services, see [DEPLOYMENT.md](DEPLOYMENT.md)

Quick summary:
1. Follow DEPLOYMENT.md for complete setup
2. Use Gunicorn instead of development server
3. Setup Systemd services for automatic startup
4. Use Nginx as reverse proxy
5. Enable SSL with Let's Encrypt

## Architecture Overview

```
┌─────────────────────────────────────────┐
│         Managers (Sonarr, etc)          │
│          API Polling / Webhooks         │
└──────────────────┬──────────────────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │    Harpoon Django   │
         │   (Main Interface)  │
         └─────────────────────┘
         ▲           │            ▲
         │           │            │
    ┌────┴───┐   ┌──┴────┐   ┌───┴────┐
    │  Redis │   │ SQLite│   │ Static │
    │  Queue │   │  DB   │   │ Files  │
    └────────┘   └───────┘   └────────┘
    
    Celery Worker ──────┐
    (File Transfer)     │
    Celery Beat ────────┼─► RTorrent / SABnzbd
    (Scheduler)         │
                        └─► SFTP ──► Seedbox
```

## API Endpoints

All endpoints require admin authentication.

- `GET /` - Dashboard
- `GET /queue/` - Queue view
- `GET /history/` - History (with ?show_archived=true toggle)
- `POST /item/<hash>/archive/` - Archive item
- `POST /item/<hash>/unarchive/` - Unarchive item
- `POST /archive/failed/` - Archive all failed
- `POST /archive/completed/` - Archive all completed
- `GET /admin/` - Django admin

## Support

For issues or questions:
- Check logs in working directory
- Review Django error messages
- Check Celery task status
- Consult DEPLOYMENT.md for production setup

## Next Steps

1. ✅ Start services (Django, Celery, Redis)
2. ✅ Configure managers and downloaders
3. ✅ Set up SFTP seedbox credentials
4. ✅ Configure download folders
5. ✅ Test with a manual grab from a manager
6. ✅ Monitor Queue and History pages
7. 📚 For production, follow [DEPLOYMENT.md](DEPLOYMENT.md)
