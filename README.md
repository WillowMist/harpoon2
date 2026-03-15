# Harpoon 2

A modern Django-based download manager that monitors directories for torrents and NZBs, sends them to remote download clients (RTorrent, SABnzbd), and handles post-processing with media management clients (Sonarr, Radarr, Lidarr, Readarr, Whisparr).

## Quick Start (Docker Compose)

```bash
# Clone the repository
git clone https://github.com/WillowMist/harpoon2
cd harpoon2

# Copy and configure docker-compose
cp docker-compose.example.yml docker-compose.yml

# Create .env file with your settings
cat > .env << EOF
SECRET_KEY=your-secret-key-here
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1,harpoon2
CSRF_TRUSTED_ORIGINS=https://your-domain.com
EOF

# Start the application
docker-compose up -d

# Access at http://localhost:4277
```

## Features

- **Blackhole Manager**: Monitor a folder for `.torrent` and `.nzb` files
- **Multiple Downloaders**: RTorrent and SABnzbd support
- **SFTP Transfer**: Automatically retrieve completed downloads
- **Archive Extraction**: Built-in ZIP and RAR extraction
- **Media Server Integration**: Post-process with Sonarr, Radarr, Lidarr, Readarr, Whisparr
- **Real-time Dashboard**: AJAX polling for live status updates

## Configuration

After starting, configure through the web UI:

1. **Seedboxes**: Add your remote seedbox (SFTP credentials)
2. **Downloaders**: Add RTorrent or SABnzbd instances
3. **Managers**: Add Blackhole or *Arr instances (Sonarr, Radarr, etc.)
4. **Download Folders**: Configure where files end up

## Docker Compose Services

- **Harpoon2**: Main application (port 4277)
- **Redis**: Celery broker

## Development Setup

```bash
# Clone and setup
git clone https://github.com/WillowMist/harpoon2
cd harpoon2

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy settings template
cp harpoon2/settings_template.py harpoon2/settings.py

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run server
python manage.py runserver
```

## Requirements

- Docker & Docker Compose (recommended)
- OR Python 3.12+ with Django 5.2

## License

MIT
