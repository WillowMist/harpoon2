# Harpoon 2

A Django-based download manager that monitors directories for torrents and NZBs,
sends them to remote download clients (RTorrent, SABnzbd, qBittorrent, AirDC++),
transfers completed downloads to local storage over SFTP, and notifies media
managers (Sonarr, Radarr, Lidarr, Readarr, Whisparr, Mylar3, Bindery, Blackhole)
so they can import and process the files.

## Quick Start (Docker Compose)

```bash
git clone https://github.com/WillowMist/harpoon2
cd harpoon2

cp docker-compose.example.yml docker-compose.yml
cp .env.example .env
# Edit .env (SECRET_KEY, ALLOWED_HOSTS, POSTGRES_PASSWORD, etc.)

docker compose up -d
# Web UI: http://localhost:4277
```

For full install steps (bare-metal, Postgres, Nginx, systemd, GitHub OAuth),
see **[USER_GUIDE.md](USER_GUIDE.md)**.

## Supported integrations

**Managers** (media / book managers that Harpoon2 notifies when files are ready):
Sonarr, Radarr, Lidarr, Readarr, Whisparr, Mylar3, Bindery, Blackhole.

**Downloaders** (clients that Harpoon2 polls for completed downloads and pulls
files from via SFTP): RTorrent, SABnzbd, qBittorrent, AirDC++.

See **[USER_GUIDE.md → Manager types](USER_GUIDE.md#manager-types)** and
**[→ Downloader types](USER_GUIDE.md#downloader-types)** for per-type notes.

## Features

- Polls remote download clients for completed downloads
- SFTP-transfers completed files from a seedbox to local storage
- Archive extraction (ZIP, RAR) in place after transfer
- Per-manager post-processing (calls the right API on Sonarr/Radarr/etc.
  with the staged path; for Bindery handles folder staging, manual-import,
  and cleanup of stale Bindery queue rows)
- Stalled-transfer detection and automatic retry of failed post-processing
- Real-time dashboard, queue, and history with live AJAX updates
- Optional Blackhole manager that watches a directory for `.torrent`/`.nzb` files

## Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)** — full installation, configuration, and
  field reference (managers, downloaders, seedboxes, folders, transfer pipeline,
  Bindery specifics)
- [DEPLOYMENT.md](DEPLOYMENT.md) — production deployment, systemd, Nginx, SSL
- [DOCKER.md](DOCKER.md) — Docker image build details, entrypoint behaviour
- [QUICKSTART.md](QUICKSTART.md) — local development quick start (Django +
  Celery + Redis on the host)
- [POSTGRESQL_MIGRATION.md](POSTGRESQL_MIGRATION.md) — migrating from SQLite
  to PostgreSQL
- [MYLAR3_API.md](MYLAR3_API.md) — Mylar3-specific API notes
- [AGENTS.md](AGENTS.md) — internal conventions for contributors

## Requirements

- Docker & Docker Compose (recommended), or
- Python 3.12+ with the dependencies in `requirements.txt` (install dev/test deps with `pip install -r requirements-dev.txt`)

## License

MIT
