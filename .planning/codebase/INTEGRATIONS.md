# External Integrations

**Analysis Date:** 2026-08-28

> All credentials are sourced from the database (`Manager.apikey`, `Downloader.options`, `Seedbox.password`/`ssh_key`) populated at runtime via Django admin forms, **not** from `.env`. `.env.example` only documents Django-level variables (`SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `REDIS_URL`, `DB_*`). Secret values are stored in DB fields — see `entities/models.py:17-71` and `users/models.py:15-18`.

## APIs & External Services

**Media Managers (the "managers" — what users add to their watchlist):**

- **Sonarr** — TV series
  - Base URL: `{manager.url}/api/v3` (`entities/managers.py:12, 198`)
  - Auth: `X-Api-Key` header
  - Endpoints used: `GET /system/status` (test), `GET /queue`, `GET /history?pageSize=50` (grabbed events), `DELETE /queue/bulk` (reject/blacklist failed download), `POST /command` with `DownloadedEpisodesScan` payload
  - `downloadClientID` in command payload is `Item.clientid` (the arr queue row id, not a downloader id)
- **Radarr** — Movies
  - Same `/api/v3` pattern as Sonarr (`entities/managers.py:252-300`); uses `DownloadedMoviesScan` instead of `DownloadedEpisodesScan`
- **Lidarr** — Music
  - Base URL: `{manager.url}/api/v1` (`entities/managers.py:305`)
- **Readarr** — Books
  - Base URL: `{manager.url}/api/v1` (`entities/managers.py:363`)
- **Whisparr** — Adult content
  - Base URL: `{manager.url}/api/v3`; `entities/managers.py:420`; falls back to `sourceTitle` as the download hash when `downloadId` is empty (Blackhole flows)
- **LazyLibrarian** — listed in `MANAGER_TYPES` (`harpoon2/settings_template.py:289`); shares the same `/api/v3` polling as the other arr-style managers
- **Mylar3** — Comics
  - URL pattern: `{manager.url}{http_root}/api?apikey={apikey}&cmd={command}` (`entities/managers.py:479-482`)
  - No `/api/v3/queue` endpoint — Harpoon2 polls Mylar3's `getLogs` API and parses log messages for the substring `Attempting to download` to detect grabs (`entities/managers.py:619-718`)
  - Detects the originating downloader from log prefix tokens like `[SABNZBD]`, `[AIRDCPP]`, `[RTORRENT]`, `[QBITTORRENT]`
  - Documented in `MYLAR3_API.md`
- **Bindery** — Books/audiobooks (planned Readarr replacement; v1-compatible)
  - Local instance referenced: `http://192.168.1.77:8787`
  - Base URL: `{manager.url.rstrip('/')}/api/v1` (`entities/managers.py:969`)
  - Auth: `X-Api-Key` header
  - Endpoints used: `GET /health` (test), `GET /queue?pageSize=100` (Bindery-native, **not** the arr-compatible `/api/queue`)
  - Post-processing: `POST /api/v1/queue/manual-import` with `{path, bookId, format}` — `bookId` is stored in `Item.clientid`
  - Stays in sync with downloader-side downloads via the downloader's `sabnzbdNzoId`/`torrentId` matching `Item.hash`
  - Status mapping: `downloading→Grabbed`, `downloaded/importPending/importing→PostProcessing`, `imported→Completed`, `failed/importFailed/importBlocked→Failed` (`entities/managers.py:945-954`)
  - Configurable transient-error substring (default: `"the download may still be finishing"`) re-routes `importFailed/importBlocked` back to `PostProcessing` so SFTP races with Bindery's first manual-import attempt can self-heal
  - Per-manager JSON-config fields on `entities/models.py:34-57`: `bindery_ebook_folder`, `bindery_ebook_category`, `bindery_audiobook_folder`, `bindery_audiobook_category`, `bindery_path_remap` (comma-separated `from:to` prefixes, longest-match-first), `bindery_transient_error_substring`
- **Blackhole** — directory-watcher manager
  - No external API. Polls `manager.monitor_directory` (optionally recursive) for `.nzb` and `.torrent` files and hands them to the configured `torrent_downloader` / `nzb_downloader`
  - Subfolder name is used as downloader category when `monitor_subdirectories=True`
  - Implementation: `entities/managers.py:1531-1758`; config fields: `entities/models.py:60-71`

**Downloaders (clients that actually fetch the bits):**

- **RTorrent** (`entities/downloaders/rtorrent.py`)
  - Transport: HTTP(S) XML-RPC via `lib/rtorrent.RTorrent(address)` constructed from `https://user:pass@host:port/<url_path>` (default path `destron23/RPC1`) (`entities/downloaders/rtorrent.py:157-185`)
  - SCGI transport is available in `lib/rtorrent/lib/xmlrpc/clients/scgi.py` (vendored from `rtorrent-python`) and exercised in `test_scgi.py`, but production code uses HTTP XML-RPC
  - Methods used: `d.multicall2`, `d.name`, `d.size_bytes`, `d.completed_bytes`, `d.complete`, `d.ratio`, `d.directory`, `d.is_multi_file`, `d.base_path`, `d.erase`, `load.raw`, `load.raw_start`, `load.magnet`
  - Per AGENTS.md, single-file torrent detection must use `d.is_multi_file()` + `d.base_path()` rather than `f.multicall()` (returns empty for some single-file torrents in some rTorrent versions)
- **SABnzbd** (`entities/downloaders/sabnzbd.py`)
  - Transport: HTTPS-capable `requests.Session` (SSL verification disabled — `client.verify = False` at `entities/downloaders/sabnzbd.py:39`)
  - Endpoints: `{baseurl}/api?apikey={key}&mode={mode}&output=json` — `mode` values: `addfile`, `addurl`, `queue`, `history`
  - Options stored in DB: `url`, `apikey`, `cleanup`, `enabled`
  - Category support for multi-category queue routing
- **qBittorrent** (`entities/downloaders/qbittorrent.py`)
  - Uses `qbittorrentapi.Client` (the `qbittorrent-api` PyPI package)
  - SSL verification disabled (`REQUESTS_ARGS={'verify': False}`)
  - Options: `host`, `port`, `username`, `password`, `use_ssl`
  - Includes 30-minute ban-backoff: when qBittorrent returns a `banned` error, further auth attempts are skipped for `BAN_BACKOFF_SECONDS = 1800` to avoid extending the ban (`entities/downloaders/qbittorrent.py:23, 73-78`)
  - Torrent files decoded locally with `bencoder` (for hash extraction)
- **AirDC++** (`entities/downloaders/airdcpp.py`)
  - Transport: HTTP(S) REST under `{scheme}://{host}:{port}/api/v1` with HTTP basic auth (`entities/downloaders/airdcpp.py:9-31`)
  - Endpoints: `/transfers`, `/transfers/{id}`, `/downloads`, `/finished-bundles`, `/events/{limit}`, `/queue/finished` (for bundle completion)
  - Default port 5600; SSL optional via `use_https`
  - Documented as the exception in the shared transfer pipeline — does its own file fetching off the seedbox

## Data Storage

**Databases:**
- **Primary:** PostgreSQL 16 (`postgres:16-alpine` sidecar in `docker-compose.yml:4-19`)
  - Driver: `psycopg2-binary>=2.9` (`requirements.txt:4`)
  - Connection env: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` (defaults `postgres:5432/harpoon/harpoon/harpoon-default-password` — see `harpoon2/settings_template.py:133-142` and `docker-compose.yml:46-55`)
  - Selected when `USE_POSTGRES=true` or `DB_PASSWORD` is set
- **Fallback (non-Docker / dev):** SQLite at `/data/harpoon2.db` (`harpoon2/settings_template.py:144-150`)
- **ORM:** Django ORM exclusively. Custom user model: `users.CustomUser` extending `AbstractUser` (`AUTH_USER_MODEL = 'users.CustomUser'`, `harpoon2/settings_template.py:68`)

**File Storage:**
- Local filesystem only
- `/data/` is the persistent root inside the container (mounted as the `harpoon2-data` Docker volume in `docker-compose.yml:62`)
- Subpaths used: `/data/settings.py` (symlink target), `/data/harpoon2.db` (SQLite), `/data/static/` (collected static), `/data/media/` (uploads), `/data/celerybeat-schedule` (Celery beat DB)
- Source/destination paths for transfers live in `Manager.folder` and `Manager.temp_folder` (DB fields) and the seedbox's `base_download_folder` (per `entities/models.py:9-12, 60-71, 134-148`)

**Caching:**
- Django's default per-process cache (no shared cache backend configured)
- `CachedDownloaderStatus` table (`entities/models.py:153-161`) caches the last `active_downloads` JSON for each `Downloader`, refreshed by the `cache_downloader_status` beat task every 10 s — this is the "fast page load" path used by the dashboard
- `django.core.cache.cache` used inline for Mylar3 log-polling state (`mylar3_{id}_last_log_time` at `entities/managers.py:634`)

## Authentication & Identity

**Auth Provider:**
- Django's built-in auth (`django.contrib.auth`), with a custom user model `users.CustomUser` (`users/models.py:15-23`)
- Custom login view at `/login/` (`harpoon2/urls.py:11`, `harpoon2/views.py`); the registration form auto-shows a "Create Account" form if no superuser exists
- `LOGIN_URL='/login/'`, `LOGIN_REDIRECT_URL='/'`, `LOGOUT_REDIRECT_URL='/login/'` (`harpoon2/settings_template.py:108-110`)
- `@login_required` is the gating decorator on every protected view (dashboard, config pages, queue, history, search, API endpoints)
- All other managers/downloaders use **API key auth via their own service** (Sonarr/Radarr/etc. send `X-Api-Key`; rTorrent/qBittorrent/AirDC++ use HTTP basic auth or session login)
- Seedbox SFTP uses username + password **or** username + SSH key (selected per `Seedbox.auth_type`: `password` | `key`, `entities/models.py:135-147`)
- No OAuth/OIDC/SAML/LDAP integration is configured. No third-party identity provider

## Monitoring & Observability

**Error Tracking:**
- **None.** No Sentry, Datadog, Rollbar, etc. is integrated. All errors are logged to `/var/log/harpoon2/` via Django logging

**Logs:**
- Django logging configured in `harpoon2/settings_template.py:196-250`
- Handlers: `console` (StreamHandler to stdout) + `file` (RotatingFileHandler at `/var/log/harpoon2/django.log`, 10 MB × 5 backups, DEBUG level)
- Loggers: `django` (INFO), `itemqueue` (DEBUG), `entities` (DEBUG), `celery` (DEBUG), plus root at DEBUG
- Process logs from `supervisord`: `/var/log/harpoon2/{supervisord,celery-worker,celery-beat,django}.log`
- The `api/version/` endpoint (`harpoon2/views.py:1019-1059`) checks `https://api.github.com/repos/WillowMist/harpoon2/releases/latest` to surface update availability

**Notifications:**
- In-app only: `users.Notification` rows are created for the first superuser on errors (download failures, SFTP failures, RAR/ZIP failures, post-process failures, manual intervention, completion) — see `users/models.py:77-143` and `users/signals.py`
- 14 boolean switches on `users.NotificationSettings` toggle which event types generate notifications
- No email, Telegram, Slack, Discord, Pushover, ntfy, or webhook delivery

## CI/CD & Deployment

**Hosting:**
- Self-hosted via Docker Compose (`docker-compose.yml`, `docker-compose.example.yml`)
- Single published image: `ghcr.io/willowmist/harpoon2` (tagged with `_version.py` value and `latest`)

**CI Pipeline:**
- GitHub Actions at `.github/workflows/docker.yml`
- Triggers: push to `master` or `workflow_dispatch`
- Steps: checkout (`actions/checkout@v4`), Docker Buildx (`docker/setup-buildx-action@v3`), login to GHCR via `secrets.PAT` (`docker/login-action@v3`), build and push with GHA cache (`docker/build-push-action@v5`)
- Permission scopes: `contents: read`, `packages: write`, `actions: write`, `id-token: write`
- No automated tests in the pipeline — only the image build

## Environment Configuration

**Required env vars (operator-supplied):**
- `SECRET_KEY` — Django secret
- `DB_PASSWORD` — PostgreSQL password (defaults to `harpoon-default-password` in compose, must be overridden in production)
- `ALLOWED_HOSTS` — comma-separated, e.g. `localhost,127.0.0.1,harpoon2,your-domain.com`
- `CSRF_TRUSTED_ORIGINS` — comma-separated HTTPS origins (needed behind a reverse proxy)
- `REDIS_URL` — defaults to `redis://redis:6379/0` inside the compose network
- `DEBUG` — `False` in production
- `USE_POSTGRES` — `true` in Docker, `false`/unset for local SQLite dev

**Secrets location:**
- Database (`Manager.apikey`, `Downloader.options`, `Seedbox.password`, `Seedbox.ssh_key`) — populated via Django admin forms, never read from env
- `.env` (gitignored) — only carries the operator-level secrets listed above
- `harpoon2/secrets.py.sample` exists in the tree but `harpoon2/secrets.py` is gitignored (`AGENTS.md` notes it should not be committed)

## Webhooks & Callbacks

**Incoming:** **None.** No webhook endpoints are registered in `harpoon2/urls.py` or any `urls.py` under the apps. Manager→Harpoon2 communication is **polling only** (Celery beat tasks poll every 20 s). Confirmed by absence of `webhook`/`hook` matches anywhere under `*.py`

**Outgoing:** **None.** No code path POSTs a Harpoon2-originated webhook to an external service. All outbound calls are pull/poll requests (manager API queries) or push-triggered via `manager.post_process(item, download_path)` after a transfer completes

---

*Integration audit: 2026-08-28*
