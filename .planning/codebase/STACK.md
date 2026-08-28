# Technology Stack

**Analysis Date:** 2026-08-28

## Languages

**Primary:**
- Python 3.12 (Docker base image: `python:3.12-slim` per `Dockerfile:1`; runtime also pinned via `Pipfile` `python_version = "3.8"` and `requirements.txt` lower bounds; the deployed container is the source of truth)
- SQL — used via Django ORM with PostgreSQL/SQLite backends
- HTML5 / Django template language (DTL) — server-rendered UI in `templates/`
- JavaScript (vanilla + jQuery) — client-side UI interactions; no bundler, no TypeScript, no framework build step

**Secondary:**
- Bash — `entrypoint.sh`, `supervisord.conf` orchestrating services
- YAML — `docker-compose.yml`, GitHub Actions workflow at `.github/workflows/docker.yml`
- CSS — bundled locally under `static/css/` (Bootstrap v4.4.1 + SB Admin v6) with per-theme overrides

## Runtime

**Environment:**
- Docker container running `python:3.12-slim` (Debian bookworm base, with `non-free` added at build time so `unrar` is available — see `Dockerfile:7-15`)
- All three processes (Django dev server, Celery worker, Celery beat) run inside a single container supervised by `supervisord` (`supervisord.conf`)

**Package Manager:**
- `pip` driven by `requirements.txt` (pinned lower bounds, upper bounds for most packages — see `requirements.txt`)
- Legacy `Pipfile` + `Pipfile.lock` present but the container installs from `requirements.txt` at build time (`Dockerfile:17-18`)

**Lockfile:** `Pipfile.lock` is present but not used by the Docker build. `requirements.txt` is the canonical install spec.

## Frameworks

**Core:**
- Django 5.2 (installed via `requirements.txt:1` `Django>=5.2,<6.0`; `INSTALLED_APPS` and `MIDDLEWARE` in `harpoon2/settings_template.py:36-61`; `wsgi.py` exposes `application`; `asgi.py` is present but unused)
- Django REST Framework is **declared in `requirements.txt:5`** (`djangorestframework>=3.14`) but **not listed in `INSTALLED_APPS`** in `harpoon2/settings_template.py:36-50` — it is not actively used. All "API" endpoints are plain Django views returning `JsonResponse` (see `harpoon2/urls.py:27-32`)

**Task Queue / Async:**
- Celery 5.x (`requirements.txt:2`, `harpoon2/celery.py`); broker and result backend are both Redis
- `django-celery-beat` for scheduled tasks; schedule defined in `harpoon2/celery.py:29-70` (poll managers / blackhole / assign items / check downloaders / stalled transfers / failures every 20 s; downloader status cache every 10 s; session cleanup daily at 03:00)
- `django-celery-results` listed but not in `INSTALLED_APPS` — currently unused

**Forms / UI:**
- `django-crispy-forms` + `crispy-bootstrap5` (CRISPY_TEMPLATE_PACK = 'bootstrap5' in `harpoon2/settings_template.py:63-64`)
- `django-crisp-modals` (modal forms)
- `django-bootstrap-modal-forms` (legacy, still imported in places)

**Search:**
- `django-watson` (full-text search over `Item`; `manage.py installwatson` invoked from `entrypoint.sh:98`); `dplibs/search.py` provides the URL pattern

**Static Files:**
- `whitenoise` middleware + `CompressedManifestStaticFilesStorage` (`harpoon2/settings_template.py:303-304`)

**Other:**
- `django-extensions` declared (`requirements.txt:8`) but not in `INSTALLED_APPS`

## Key Dependencies

**Critical (application logic):**
- `requests>=2.32` — HTTP client used everywhere (`entities/managers.py:1`, `entities/downloaders/sabnzbd.py:2`, `entities/downloaders/airdcpp.py:1`, etc.). No `httpx`, no `urllib3` direct calls outside `requests`
- `paramiko>=3.0` — SFTP/SSH client for seedbox file transfers (`itemqueue/tasks.py:8,377-395`); supports both password and SSH-key auth
- `psycopg2-binary>=2.9` — PostgreSQL driver
- `redis>=5.0` — Celery broker client
- `pynacl>=1.5`, `cryptography>=44.0`, `cffi>=1.17` — pulled in by `paramiko` and `bencoder`
- `python-dateutil>=2.9`, `pytz>=2024.0` — datetime handling
- `billiard>=4.0` — Celery's process pool
- `click>=8.0`, `kombu>=5.4`, `certifi>=2024.0` — Celery transitive deps

**Downloader-specific:**
- `qbittorrent-api>=2024.3` (`entities/downloaders/qbittorrent.py:36`) — qBittorrent Web API client
- `bencoder>=0.2` (`entities/downloaders/qbittorrent.py:37,121`) — torrent file decoder
- `rtorrent-python` is in `Pipfile` (legacy). The active RTorrent integration uses an in-tree `lib/rtorrent/` package (vendored fork of the `rtorrent-python` library) loaded as `from lib.rtorrent import RTorrent` in `entities/downloaders/rtorrent.py:177`. SCGI and HTTP XML-RPC transports live in `lib/rtorrent/lib/xmlrpc/`
- `pysftp` is in `Pipfile` but **not used at runtime** — SFTP is done via `paramiko` directly (`itemqueue/tasks.py:394-395`)

**Utility:**
- `whitenoise>=6.6` — static file serving
- `django-celery-beat>=2.9` — beat schedule persistence in DB

**Build/CI (not in app runtime):**
- `bump2version` — version bumping driven by `.bumpversion.cfg`

## Configuration

**Environment:**
- All non-default settings read from environment variables in `harpoon2/settings_template.py`. Documented in `.env.example`
- `settings.py` is a symlink to `harpoon2/settings_template.py` created by `entrypoint.sh:13-17`; the template is the canonical source
- Critical env vars (see `harpoon2/settings_template.py:24-32, 129-158` and `docker-compose.yml:46-55`):
  - `SECRET_KEY` (Django)
  - `DEBUG` (boolean string)
  - `ALLOWED_HOSTS` (comma-separated)
  - `CSRF_TRUSTED_ORIGINS` (comma-separated)
  - `USE_POSTGRES` (boolean string; toggles PostgreSQL vs SQLite backend)
  - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
  - `REDIS_URL` (full broker URL) and `REDIS_HOST`, `REDIS_PORT`
  - `DOCKER_TAG` (set at build time, surfaced in `api/version/` response)

**Build:**
- `Dockerfile` — multi-stage single image; installs `gcc`, `libffi-dev`, `libpq-dev` (build deps for `psycopg2`/`cryptography`), `unrar` (for archive extraction), `supervisor`
- `entrypoint.sh` — waits for Postgres/Redis with retry loops, runs `makemigrations`/`migrate`/`installwatson`/`collectstatic`, then `exec supervisord`
- `supervisord.conf` — manages `celery-worker`, `celery-beat`, and `django` (`runserver 0.0.0.0:4277`) as supervised programs
- `manage.py` — standard Django entry point

**Static / Media:**
- `STATIC_URL='/static/'`, `STATIC_ROOT='/data/static/'`
- `MEDIA_URL='/media/'`, `MEDIA_ROOT='/data/media/'`
- `STATICFILES_DIRS=[BASE_DIR/'static']` (in-tree assets in `static/`)
- `WHITENOISE_AUTOREFRESH=True` for dev; `WHITENOISE_USE_FINDERS=True`

## Platform Requirements

**Development:**
- Docker + Docker Compose v2
- Port 4277 (Harpoon2 web UI), 6379 (Redis — internal network only in compose)
- `.env` file (gitignored; template in `.env.example`) supplying `SECRET_KEY`, `DB_PASSWORD`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`

**Production:**
- Single container image published by `.github/workflows/docker.yml` to `ghcr.io/willowmist/harpoon2` with tags `:latest` and `:<version-from-_version.py>` on every push to `master` (or manual `workflow_dispatch`)
- Image builds with `docker/build-push-action@v5`, GHA cache, logs in via `docker/login-action@v3` using `secrets.PAT`
- PostgreSQL 16 (alpine) and Redis 7 (alpine) sidecars — declared in `docker-compose.yml` with `pg_isready` and `redis-cli ping` healthchecks and `depends_on: condition: service_healthy`
- Reverse proxy (nginx) is **commented out** in `docker-compose.yml:78-91` — TLS termination is the operator's responsibility. `CSRF_TRUSTED_ORIGINS` is the env var used to whitelist the public origin
- `harpoon2-data` Docker volume persists `/data` (settings.py symlink target, SQLite fallback, static, media, celerybeat schedule)
- Supervised processes auto-restart on crash (`autorestart=true` in `supervisord.conf`)

---

*Stack analysis: 2026-08-28*
