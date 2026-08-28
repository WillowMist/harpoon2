<!-- refreshed: 2026-08-28 -->
# Architecture

**Analysis Date:** 2026-08-28

## System Overview

Harpoon2 is a Django-based download manager that orchestrates a multi-step pipeline: it polls *arr-style media managers (Sonarr, Radarr, Lidarr, Readarr, Whisparr, Bindery, Mylar3) and folder-watching Blackhole managers, assigns incoming grabs to configured download clients (RTorrent, SABnzbd, AirDC++, QBittorrent), SFTP-transfers finished files from a seedbox into local staging folders, extracts archives, and triggers per-manager post-processing APIs. The app is split into three Django apps: `harpoon2` (project / config / dashboard), `entities` (manager + downloader adapters + scheduler tasks), and `itemqueue` (item / transfer models + transfer tasks). Background work runs through Celery (Redis broker) under supervisord.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                      Django (harpoon2/views.py)                         │
│   dashboard / queue / history / settings / managers / downloaders      │
│   `harpoon2/urls.py`, `harpoon2/template_content.py`                    │
└──────────────────┬──────────────────┬──────────────────┬───────────────┘
                   │                  │                  │
                   ▼                  ▼                  ▼
┌──────────────────────────┐ ┌─────────────────────┐ ┌──────────────────┐
│ entities (CRUD + tests)  │ │ itemqueue (transfers)│ │ users (prefs,    │
│ `entities/views.py`      │ │ `itemqueue/views.py` │ │ notifications)   │
│ `entities/forms.py`      │ │ `itemqueue/tasks.py` │ │ `users/views.py` │
│ `entities/urls.py`       │ │ `itemqueue/models.py`│ │ `users/models.py`│
└──────────┬───────────────┘ └──────────┬──────────┘ └────────┬─────────┘
           │                            │                     │
           ▼                            ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Celery Beat schedule                            │
│  poll-managers (20s)  check-downloaders (20s)  assign-items (20s)       │
│  poll-blackhole (20s) check-stalled-transfers (20s)                     │
│  cache-downloader-status (10s)  check-downloader-failures (5m)         │
│  cleanup-sessions (3am daily)                                           │
│  `harpoon2/celery.py`                                                   │
└──────────────────┬───────────────────────────────────────────────────────┘
                   │
        ┌──────────┼──────────┬────────────────┬─────────────────┐
        ▼          ▼          ▼                ▼                 ▼
┌────────────┐┌────────────┐┌────────────┐┌──────────────┐┌──────────────┐
│ Manager    ││ Blackhole  ││ Assign      ││ Transfer     ││ Cache        │
│ poll       ││ folder scan││ items to    ││ SFTP +       ││ downloader   │
│ `entities/ ││ `entities/ ││ downloaders ││ extract      ││ status       │
│ tasks.py`  ││ tasks.py`  ││ `entities/  ││ `itemqueue/  ││ `entities/   │
│            ││            ││ tasks.py`   ││ tasks.py`    ││ tasks.py`    │
└─────┬──────┘└─────┬──────┘└──────┬──────┘└──────┬───────┘└──────┬───────┘
      │             │              │              │              │
      ▼             ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  entities/managers.py  (Sonarr, Radarr, Lidarr,         │
│                  Readarr, Whisparr, Bindery, Mylar3, Blackhole,         │
│                  LazyLibrarian + Arr base)                              │
│                  entities/downloaders/* (BaseDownloader + RTorrent,     │
│                  SABnzbd, AirDC++, QBittorrent)                         │
└──────────────────┬──────────────────────────────────┬───────────────────┘
                   │                                  │
                   ▼                                  ▼
┌──────────────────────────────────┐ ┌────────────────────────────────────┐
│ External media-manager APIs      │ │ External download clients           │
│ Sonarr / Radarr / Lidarr /       │ │ RTorrent (XML-RPC), SABnzbd (HTTP), │
│ Readarr / Whisparr / Bindery /   │ │ AirDC++ (HTTP), QBittorrent (HTTP)  │
│ Mylar3 / LazyLibrarian           │ │ + seedbox SFTP (paramiko)           │
└──────────────────────────────────┘ └────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| `harpoon2.celery` | Defines `Celery('harpoon2')` instance, broker config, and the beat schedule | `harpoon2/celery.py` |
| `harpoon2.views` | Dashboard, queue, history pages, JSON APIs (`api_dashboard`, `api_queue`, `api_history`, `api_item_*`, `api_version_check`), archive/cancel/retry actions, login view | `harpoon2/views.py` |
| `harpoon2.urls` | Root URL routes; delegates `/entities/...` and `/users/...` to those apps, mounts `/search/` from `dplibs.search` | `harpoon2/urls.py` |
| `harpoon2.middleware` | `UserTimeZone` middleware activates the user's tz on every request | `harpoon2/middleware.py` |
| `harpoon2.template_content` | Custom context processor: branding, interface (theme), `__version__`, IP | `harpoon2/template_content.py` |
| `harpoon2.tasks` | `cleanup_sessions` daily task | `harpoon2/tasks.py` |
| `entities.models` | ORM models: `DownloadFolder`, `Manager`, `Downloader`, `Seedbox`, `CachedDownloaderStatus`. `Manager.from_db` and `Downloader.from_db` attach a `.client` adapter | `entities/models.py` |
| `entities.managers` | Per-manager-type adapters with `check_queue`, `post_process`, `test` and (for Mylar3) `poll`. Arr subclass hierarchy: `Arr → Sonarr | Radarr | Lidarr | Readarr | Whisparr`; standalone: `Mylar3`, `Bindery`, `Blackhole`, `LazyLibrarian` | `entities/managers.py` |
| `entities.downloaders` package | Download-client adapter package; `BaseDownloader` (abstract) and one module per client. Maps display names to attribute names (`AirDC++ → AirDCpp`) via `DOWNLOADER_NAME_MAP` | `entities/downloaders/base.py`, `entities/downloaders/rtorrent.py`, `entities/downloaders/sabnzbd.py`, `entities/downloaders/airdcpp.py`, `entities/downloaders/qbittorrent.py`, `entities/downloaders/__init__.py` |
| `entities.forms` | Modal forms: `DLFolderModalForm`, `ManagerModalForm`, `DownloaderModalForm`, `SeedboxModalForm` | `entities/forms.py` |
| `entities.views` | CRUD views (modal create/update/delete) for Manager / Downloader / Seedbox / DownloadFolder; settings/managers/downloaders pages; test endpoints | `entities/views.py` |
| `entities.urls` | App URL routes (settings, managers, downloaders, seedboxes, ajax endpoints) | `entities/urls.py` |
| `entities.tasks` | Celery tasks: `poll_managers`, `poll_manager`, `poll_blackhole_managers`, `poll_blackhole_manager`, `assign_items_to_downloaders`, `cache_downloader_status` | `entities/tasks.py` |
| `entities.apps.EntitiesConfig.ready` | Startup hook: re-assigns downloaders to items stuck in `PostProcessing` without a downloader | `entities/apps.py` |
| `itemqueue.models` | ORM models: `Item` (primary key = `hash`), `ItemHistory`, `FileTransfer` | `itemqueue/models.py` |
| `itemqueue.tasks` | Celery tasks: `transfer_files_async` (SFTP + extract + manager post_process), `postprocess_item`, `check_downloaders`, `check_stalled_transfers`, `retry_postprocessing`, `check_downloader_failures`. Also defines `process_rar_archives`, `process_zip_archives`, `find_rar_archives`, `extract_rar_archive`, `find_zip_archives`, `extract_zip_archive` | `itemqueue/tasks.py` |
| `users.models` | `CustomUser` (extends `AbstractUser`; adds `interface`, `prefs`, `timezone`), `NotificationSettings` (per-type booleans), `Notification` | `users/models.py` |
| `users.views` | `userprefs` (saves prefs + notification settings), `detail`, notification JSON APIs | `users/views.py` |
| `users.signals` | `user_logged_in` handler populates session metadata | `users/signals.py` |
| `dplibs.search` | Top-level `/search/` view (name `search`); uses `Q(name__icontains) | Q(hash__icontains) | Q(category__icontains)` | `dplibs/search.py` |
| `dplibs.session` | Helper utilities (`clear_inactive_sessions`, `get_sessions`) used by `users.models.CustomUser.get_active_sessions` | `dplibs/session.py` |
| `lib.rtorrent` | Vendored third-party `rtorrent-python` 0.2.9 library (XML-RPC client wrapper) used by the RTorrent downloader | `lib/rtorrent/__init__.py` |
| `entrypoint.sh` | Container entrypoint: waits for Postgres + Redis, runs `makemigrations` / `migrate`, `installwatson`, `collectstatic`, then `supervisord` | `entrypoint.sh` |
| `supervisord.conf` | Runs three processes inside the container: `celery-worker`, `celery-beat`, `django` (port 4277) | `supervisord.conf` |

## Pattern Overview

**Overall:** Layered Django MVT with a Celery-based background pipeline and a polymorphic adapter pattern over `managertype` / `downloadertype` strings.

**Key Characteristics:**
- **Polymorphic adapters via `from_db`**: `Manager.from_db` and `Downloader.from_db` attach a per-type client object (`.client`) at row fetch time so call sites use `manager.client.post_process(item, path)` rather than dispatching in every view (`entities/models.py:73-109`).
- **String-coded type, lookup-by-attribute**: Both `MANAGER_TYPES` and `DOWNLOADER_TYPES` in `harpoon2/settings.py` are string choices; `DOWNLOADER_NAME_MAP` (`entities/downloaders/__init__.py:21`) translates the display string to a Python identifier so `AirDC++` becomes `AirDCpp`.
- **Single shared transfer pipeline**: `itemqueue.tasks.transfer_files_async` is the *only* post-processing path for all manager × downloader combos except AirDC++ — see "Transfer Pipeline Architecture" below.
- **Background-driven, dashboard-driven**: Almost every state transition (queue items, transfers, extraction, post-processing, cache) is triggered by Celery beat, with the dashboard polling JSON APIs on a short cycle (`api_dashboard`, `api_queue`, `api_history`).
- **Heavy defensive logging / error tolerance**: Most manager and downloader calls wrap exceptions, return `(False, message)` tuples, and create an `ItemHistory` row rather than raising — see `entity.managers.Bindery.post_process` and `entities.managers.Mylar3.poll` for examples.

## Layers

**Presentation (Django views + templates):**
- Purpose: Render HTML pages and serve JSON for the dashboard.
- Location: `harpoon2/views.py`, `entities/views.py`, `users/views.py`, `templates/*.html`, `entities/templates/entities/*.html`, `users/templates/users/*.html`.
- Contains: View functions and CBV-based modal CRUD views; base template at `templates/base.html` with a custom theme override per Bootswatch variant (`static/css/overrides/{theme}.css`).
- Depends on: ORM models in `entities` and `itemqueue`; auth via `@login_required`.
- Used by: Browser sessions and the JSON-polling dashboard JS.

**API / Action endpoints:**
- Purpose: Mutate state outside the modal flow (cancel, archive, retry, update status, update downloader) and serve JSON for AJAX polling.
- Location: `harpoon2/urls.py` (`/api/...`, `/archive/...`, `/cancel/...`, `/retry/...`).
- Contains: Plain Django function views, no DRF; some endpoints are `@login_required` and most accept `POST` only.
- Depends on: ORM, redirect helpers, `Notification.create_for_admin`.
- Used by: Forms in templates and the dashboard's polling loop.

**Domain models (ORM):**
- Purpose: Persistent state for managers, downloaders, items, transfers, history, notifications, settings.
- Location: `entities/models.py`, `itemqueue/models.py`, `users/models.py`.
- Contains: Django `Model` subclasses with `from_db` hooks; `Item.hash` is the primary key; FKs link `Item` → `Manager` and → `Downloader`; `Downloader` → `Seedbox`.
- Depends on: `harpoon2.settings` for the `MANAGER_TYPES` / `DOWNLOADER_TYPES` choice lists.
- Used by: Views, tasks, and adapters.

**Adapters (Manager + Downloader):**
- Purpose: Per-service HTTP / XML-RPC / SFTP integration, with a uniform `test`, `check_queue`/`post_process` (managers) or `add`/`find`/`get_completed`/`verify_completion`/`get_download_info`/`cleanup` (downloaders).
- Location: `entities/managers.py`, `entities/downloaders/{base,rtorrent,sabnzbd,airdcpp,qbittorrent}.py`.
- Contains: One class per manager type and per downloader; `BaseDownloader` is an `ABC` with five `@abstractmethod` members plus default no-op implementations of `get_completed`, `verify_completion`, `get_download_info`, `cleanup` (`entities/downloaders/base.py`).
- Depends on: `requests` (HTTP), `qbittorrentapi`+`bencoder` (qBittorrent), `paramiko` (AirDC++ / Seedbox SFTP), `lib.rtorrent` (rTorrent XML-RPC).
- Used by: Tasks that poll and by the post-processing call inside `transfer_files_async`.

**Background (Celery beat + worker):**
- Purpose: Drive polling, transfer, extraction, post-processing, retries, and cache.
- Location: `harpoon2/celery.py` (beat schedule), `entities/tasks.py`, `itemqueue/tasks.py`, `harpoon2/tasks.py`.
- Contains: `@shared_task` functions; only `transfer_files_async` declares `time_limit=3600` / `soft_time_limit=3300` (`itemqueue/tasks.py:292`).
- Depends on: Models, adapters, `paramiko` for SFTP, `subprocess` for `unrar`.
- Used by: supervisord-managed `celery-worker` and `celery-beat` processes (`supervisord.conf:15-35`).

**Storage / config:**
- Purpose: Postgres (or SQLite when `USE_POSTGRES=false`); Redis for the Celery broker/result backend; `/data` mounted volume for the SQLite DB (`harpoon2/settings.py:131-150`), `STATIC_ROOT`, and `MEDIA_ROOT`.
- Location: `harpoon2/settings.py:111-150`, `Dockerfile` (creates `/opt/harpoon2`, `/data`, `/var/log/harpoon2`, `/mnt/processing`).
- Contains: Settings, a runtime-mounted SQLite-or-Postgres config; `STATIC_URL='/static/'`, `STATIC_ROOT='/data/static/'`, `MEDIA_URL='/media/'`, `MEDIA_ROOT='/data/media/'`.

## Data Flow

### Primary request path (web → DB)

1. Request hits `harpoon2/urls.py` (`/`, `/queue`, `/history`, `/archive/...`, `/cancel/...`, `/retry/...`, `/api/...`).
2. View runs in `harpoon2/views.py` (or app-level `views.py`), protected by `@login_required` for all dashboard/config pages; auth user fetched via `django.contrib.auth.get_user_model()`.
3. View queries ORM (`Item.objects.filter(...)`, `FileTransfer`, `CachedDownloaderStatus`), aggregates counts/sums for the dashboard, persists changes via `item.save()`, and records state transitions in `ItemHistory`.
4. State-mutating endpoints write through to the same models the Celery tasks read; e.g. `update_item_status` mutates `Item.status` and emits an `ItemHistory` row (`harpoon2/views.py:573-601`).

### Background pipeline (Celery beat → managers → downloaders → SFTP → post-processing)

1. `entities.tasks.poll_managers` runs every 20 s; routes to `poll_manager(manager_id)` which branches on `managertype`:
   - Blackhole → no-op (handled by `poll_blackhole_managers`).
   - Mylar3 → `Mylar3(manager).poll()` (log scraping; logs newest-first, caches last-seen timestamp in `django.core.cache`).
   - Bindery → `Bindery(manager).check_queue()` (uses Bindery-native `/api/v1/queue`).
   - Others (`Sonarr` / `Radarr` / `Lidarr` / `Readarr` / `Whisparr`) → fetch `/api/v3/history` (or `/api/v1/history` for Lidarr/Readarr), filter `eventType == 'grabbed'`, `Item.objects.get_or_create(hash=downloadId, defaults={..., 'manager': manager, 'downloader': <resolved from queue's downloadClient>})`. Skips items older than 2 days, archives, or previously imported.
2. `entities.tasks.assign_items_to_downloaders` (every 20 s) back-fills `Item.downloader` for items in `Grabbed`, `Failed`, or `PostProcessing` that have a manager but no downloader. Bindery branches to `/api/queue` (the arr-compatible endpoint) and matches `downloadId == item.hash`; non-Bindery branches to `/api/v3/queue` (Lidarr/Readarr to `/api/v1/queue`). Falls back to fuzzy name match, then protocol-based type match.
3. `itemqueue.tasks.check_downloaders` (every 20 s) iterates `Downloader.objects.all()` and calls `client.get_completed()`. AirDC++ takes a special path (`client.process_completed()`) that consumes `/events/{n}` and creates/queues items. For each returned hash, looks up `Item.objects.get(hash__iexact=...)` and — if not already Completed/Failed/PostProcessing — queues `postprocess_item.delay(hash)`.
4. `itemqueue.tasks.postprocess_item` (per-hash) verifies completion via `client.verify_completion(hash)`, flips status `→ PostProcessing`, then `transfer_files_async.apply_async(args=[hash], countdown=5)`.
5. `itemqueue.tasks.transfer_files_async(item_hash)` (`itemqueue/tasks.py:292-1054`) is the central post-processing pipeline:
   1. `downloader.client.get_download_info(hash)` → `{remote_dir, files_to_copy, is_single_file, name}`. For RTorrent single-file torrents uses `d.is_multi_file()` + `d.base_path()` to avoid the `f.multicall()` empty-result bug.
   2. SFTP-connects to `downloader.seedbox` via `paramiko` (password or `RSAKey.from_private_key_string(seedbox.ssh_key)`).
   3. Determines destination: `item.manager.folder.folder` (with category subfolder for Blackhole), falling back to `item.downloader.options['target_folder']` (AirDC++), then `/tmp`. Blackhole uses a temp folder that is later renamed to the final folder post-extraction.
   4. Builds the transfer list — single-file vs recursive `walk_remote_sftp` walk — and **creates all `FileTransfer` records upfront** in `pending` state (deduped against existing `completed` records).
   5. Iterates the records, calls `sftp.get(remote, local, callback=progress)`, retries up to 3× on failure with reconnection, marks each `transferring` → `completed`/`failed`.
   6. Calls `process_zip_archives(local_folder, item)` then `process_rar_archives(local_folder, item)` (`itemqueue/tasks.py:84-289`); extraction uses `unrar` CLI for RAR and `zipfile` for ZIP. Updates `item.extraction_status` / `extraction_progress` and rejects via `manager.client.reject_download(item, reason)` on failure.
   7. Blackhole-only: `os.rename(temp_folder, final_folder)`.
   8. Calls `item.manager.post_process(item, download_path)` — the only per-manager divergence point:
      - Arr → `DownloadedEpisodesScan` / `DownloadedMoviesScan` / `DownloadedAlbumsScan` / `DownloadedBooksScan` commands with `downloadClientID=str(item.clientid)`.
      - Mylar3 → `forceProcess` after searching for the matching `comicid` / `issueid`.
      - Bindery → resolves the staged path from the first completed `FileTransfer`, optionally moves it into `opts_ebook_folder`/`opts_audiobook_folder`, applies longest-prefix `path_remap`, then calls `/api/v1/queue/manual-import/match` (if there's a recoverable Bindery row) and `/api/v1/queue/manual-import`. Deletes stale `importFailed`/`importBlocked` rows so they don't drag the item back to Failed.
      - Blackhole → no remote post-process; only the folder move.
   9. On success, `item.downloader.client.cleanup(first_transfer)` removes the seedbox copy. Then `item.status = 'Completed'` (Bindery items are intentionally *not* promoted to Completed if post-process failed — the Bindery poll owns their state) and `Notification.create_for_admin(...)`.
6. `itemqueue.tasks.check_stalled_transfers` (every 20 s) flags `FileTransfer` records stuck in `transferring` with no `modified` change in 5+ minutes, plus PostProcessing items with failed/pending transfers older than 5 min (resets to `Grabbed` after deleting those transfers).
7. `itemqueue.tasks.check_downloader_failures` (every 5 min, SABnzbd only) inspects the downloader's history for failed slots and notifies the manager via `manager_client.reject_download` so it can search for an alternative release.
8. `itemqueue.tasks.retry_postprocessing` runs 5 min after a failed post-process (and 10 min after a retry failure), rebuilding `download_path` from the manager's folder and re-calling `manager.post_process`.

**State Management:**
- All item lifecycle state lives on `Item.status` (`Created`, `Grabbed`, `PostProcessing`, `Completed`, `Failed`).
- Per-step audit trail in `ItemHistory.details` (a 500-char text column).
- Long-running transfer progress in `FileTransfer` (`pending` → `transferring` → `completed`/`failed`); counts surfaced via `CachedDownloaderStatus` updated by `entities.tasks.cache_downloader_status` every 10 s so the dashboard doesn't have to hit the downloader APIs.

## Key Abstractions

**Manager (per-type adapter):**
- Purpose: Wraps a single *arr / Bindery / Mylar3 / Blackhole instance behind a uniform interface used by polling and post-processing.
- Examples: `entities/managers.py:4-193` (`Arr` base + `Sonarr`/`Radarr`/`Lidarr`/`Readarr`/`Whisparr`); `entities/managers.py:469-935` (`Mylar3`); `entities/managers.py:938-1528` (`Bindery`); `entities/managers.py:1531-1755` (`Blackhole`).
- Pattern: Polymorphic dispatch via `manager.managertype` string. `Manager.from_db` constructs the right client on read; views and tasks call `manager.client.<method>(...)` so call sites don't switch on the type.

**Downloader (per-type adapter):**
- Purpose: Wraps a download client behind a uniform interface (`add`, `find`, `get_status`, `get_files`, `delete`, `test`, plus default no-op `get_completed`, `verify_completion`, `get_download_info`, `cleanup`).
- Examples: `entities/downloaders/rtorrent.py` (XML-RPC + `lib.rtorrent`), `entities/downloaders/sabnzbd.py` (HTTP API), `entities/downloaders/airdcpp.py` (HTTP API + monitoring-only mode), `entities/downloaders/qbittorrent.py` (`qbittorrentapi` + ban-backoff state).
- Pattern: Subclass `BaseDownloader` (`entities/downloaders/base.py:4`). Each downloader implements an `optionfields` dict consumed by `Downloader.checkoptions()` and by the AJAX `get_downloader_options` view (`entities/views.py:202-215`).

**Item / FileTransfer / ItemHistory:**
- Purpose: Per-grab entity + per-file progress + immutable audit log.
- Examples: `itemqueue/models.py:6-67`.
- Pattern: `Item.hash` is the natural primary key (manager queue row id, torrent hash, NZB nzo id, or md5 of the name for AirDC++). `FileTransfer.item` and `ItemHistory.item` are cascading FKs.

**Notification:**
- Purpose: Surface failures to the admin.
- Examples: `users/models.py:77-142`; created via `Notification.create_for_admin(message, notification_type, item_hash)`.
- Pattern: First superuser receives it; per-type gating via `NotificationSettings._should_notify`.

## Entry Points

**Web entry:**
- `harpoon2/wsgi.py` (WSGI) / `harpoon2/asgi.py` (ASGI). `manage.py` is the standard Django CLI (`manage.py`).
- URL root: `harpoon2/urls.py`. Dashboard `/` and `/api/dashboard/` are the primary polling endpoints (`harpoon2/views.py:80-263` for HTML, `harpoon2/views.py:672-851` for JSON).
- Login: `harpoon2/views.py:17-77` (`login_view`) — if no superuser exists, the form creates one (first-run onboarding); otherwise standard authenticate/login. `LOGIN_URL='/login/'`, `LOGIN_REDIRECT_URL='/'`, `LOGOUT_REDIRECT_URL='/login/'` (`harpoon2/settings.py:108-110`).

**Process entry (container):**
- `Dockerfile` → `ENTRYPOINT ["./entrypoint.sh"] CMD ["start"]`.
- `entrypoint.sh` waits for Postgres (`wait_for_postgres`) and Redis (`wait_for_redis`), runs `makemigrations`, `migrate`, `installwatson`, `collectstatic`, then `exec supervisord -c /opt/harpoon2/supervisord.conf`. Subcommands `django` / `worker` / `beat` / `redis` / `migrate` / `createsuperuser` / `shell` / `bash` are supported.
- `supervisord.conf` runs `celery-worker`, `celery-beat`, and `django` (`runserver 0.0.0.0:4277`) as three programs.

**Celery entry:**
- `harpoon2/celery.py:10` (`app = Celery('harpoon2')`) loads task modules via `autodiscover_tasks`. Beat schedule defined inline (`app.conf.beat_schedule`). See "Background pipeline" above.

**CLI entry:**
- `manage.py` standard Django management (run via `python manage.py …`).
- Custom commands: `entities/management/commands/assign_missing_downloaders.py` — dry-run by default, `--fix` to apply.

## Architectural Constraints

- **Threading:** Single-process Celery worker + `--concurrency` default. Celery is configured with `task_acks_late=True`, `worker_prefetch_multiplier=1` (`harpoon2/celery.py:21-22`) so long-running `transfer_files_async` tasks don't get pre-empted mid-SFTP. The Celery broker uses Redis (`harpoon2/settings.py:157-162`).
- **Global state:**
  - `Downloader.client` and `Manager.client` are attached on `from_db` and cached per-row — a per-process cache, not shared across workers (`entities/models.py:73-109`).
  - `QBittorrentDownloader` keeps a per-instance `_auth_skip_until` (epoch seconds) to back off after an IP ban (`entities/downloaders/qbittorrent.py:23-78`).
  - `Mylar3` writes the last-seen log timestamp into `django.core.cache` keyed by `mylar3_{manager.id}_last_log_time` (`entities/managers.py:634-723`).
- **Circular imports:** `entities.downloaders` is imported as a package (`entities/downloaders/__init__.py`) and re-exported via `entities/managers`; `itemqueue.tasks` and `entities.tasks` reach across each other for adapters but stay at module-scope import boundaries (e.g., `entities.tasks` imports models locally inside functions).
- **Symlink-based settings:** `harpoon2/settings.py` is a symlink to `/data/settings.py`, which is copied from `settings_template.py` at image build time (`Dockerfile:24`). The container sets `BASE_DIR = Path('/opt/harpoon2')`; running outside that path will break static / migration paths.
- **Single-file vs multi-file detection:** RTorrent uses `d.is_multi_file()` + `d.base_path()` rather than `f.multicall()` because the latter returns empty for some single-file rTorrent versions (`entities/downloaders/rtorrent.py:512-565`). Other downloaders infer single-file from filename extension.
- **Bindery-managed items are not auto-promoted to Completed** by `transfer_files_async` on post-process failure — they stay in PostProcessing so the Bindery poll (which owns the truth) can flip them to `imported`/`imported` and `Completed`.
- **`Item.clientid` is misnamed:** it stores the manager's reference ID for the record being imported (arr `queue.id` or Bindery `bookId`), not the download client id. Used as `downloadClientID` / `bookId` in post-process payloads.

## Anti-Patterns

### Anti-pattern: Live debug prints in views

**What happens:** `harpoon2/views.py:83-85` opens `import sys; print("DEBUG home: CALLED", file=sys.stderr); sys.stderr.flush()`. `harpoon2/views.py:84` is one of several `import sys` blocks inside view functions.
**Why it's wrong:** Stderr spam on every dashboard load obscures real errors and leaks into log aggregation; runs in production.
**Do this instead:** Use `logging.getLogger(__name__).debug(...)` (which is already wired to `/var/log/harpoon2/django.log` per `harpoon2/settings.py:196-250`) and gate behind `DEBUG=True`.

### Anti-pattern: Duplicated transfer code in `check_stalled_transfers`

**What happens:** `itemqueue/tasks.py:1252-1352` re-implements a Blackhole temp→final folder rename and re-runs extraction + post-process inside the stall-check loop, even though `transfer_files_async` already does all of this on its primary run (`itemqueue/tasks.py:880-1015`).
**Why it's wrong:** Two divergent code paths for the same operation; behaviour changes (e.g. Bindery-specific `bindery_pp_failed` guard) only live in one of them.
**Do this instead:** When the only difference is "no pending transfers and all completed", call `transfer_files_async.delay(item.hash)` (or extract a shared helper that returns the same result).

### Anti-pattern: Hard-coded type-discounting in `assign_items_to_downloaders`

**What happens:** `entities/tasks.py:434-460` matches clients by `name__icontains` first, then by `'sab' in download_client.lower()`, `'rtorrent' in download_client.lower()`, etc., then by `protocol`.
**Why it's wrong:** Substring matching on names like "SAB" / "RTorrent" mis-assigns when multiple clients of the same family exist; brittle when managers rename clients.
**Do this instead:** Persist the relationship at manager-config time (e.g. a `Manager.protocol → Downloader` default), or look up by the download-client id returned in the queue row.

### Anti-pattern: Shadowing the builtin `hash` in downloader code

**What happens:** `entities/downloaders/qbittorrent.py:172` does `hash = hash.upper()` inside `find`, `get_files`, `verify_completion`, `get_download_info`, `delete`. Same in `rtorrent.py`.
**Why it's wrong:** Reassigning a builtin name hides Python's `hash()` from linters and makes call sites harder to read; the parameter name is also `hash` so the local shadows the builtin within the function body only.
**Do this instead:** Rename the parameter (e.g. `info_hash`) or assign to a clearly scoped local.

### Anti-pattern: `try/except` swallowing real errors

**What happens:** `entities/managers.py:74-91` and similar blocks return `(False, e)` tuples that hide the traceback; `itemqueue/tasks.py:366-373` uses `try: ... except: pass` for folder-bundle detection.
**Why it's wrong:** When an integration breaks, the only signal in logs is a one-line "Error: …" with no traceback, making root-cause analysis much harder.
**Do this instead:** Log with `logger.exception(...)` or `exc_info=True`; keep the swallow only for genuinely non-critical branches (e.g., optional metadata).

## Error Handling

**Strategy:** Adapter methods return `(success: bool, message: str)` tuples. View code inspects the tuple and renders an HTML page (test endpoint) or JSON response. Long-running tasks wrap the body in `try/except` and emit `ItemHistory.details` lines so the audit trail shows exactly where a failure occurred.

**Patterns:**
- Per-task `ItemHistory` rows: `itemqueue/tasks.py:113, 219, 1052, 1234, 1405` and throughout.
- Notifications on user-impacting failures: `Notification.create_for_admin(...)` is called from `itemqueue.tasks` (RAR / ZIP / SFTP / post-process failures) and `entities.tasks.poll_manager` (download failures).
- Manager `reject_download(item, reason)` — sends a DELETE to the manager's `/queue/bulk` with `blacklist: True` so it searches for an alternative release (`entities/managers.py:108-145`).
- Stalled-transfer watchdog: `check_stalled_transfers` resets items with failed/pending transfers older than 5 min back to `Grabbed` after deleting those transfers.
- Retry post-processing: `retry_postprocessing.apply_async(args=[hash], countdown=300)` after a failed manager call; `countdown=600` after a retry failure.

## Cross-Cutting Concerns

**Logging:** Configured in `harpoon2/settings.py:196-250` with a `RotatingFileHandler` at `/var/log/harpoon2/django.log` (10 MB × 5 backups). Per-app loggers (`itemqueue`, `entities`, `celery`) inherit the same handlers at DEBUG level. Console output is mirrored.

**Validation:**
- Form-level: `entities/forms.py` overrides `clean`/`validate_unique` for folder paths (creates dirs on add), JSON-encodes the downloader `options` JSONField, hides seedbox password/ssh_key from re-render on edit.
- Manager-level: managers deny downloads older than 2 days (`entities/tasks.py:94-107`); Bindery rejects rows whose `errorMessage` doesn't contain the configured transient-error substring as `Failed`.

**Authentication:** Custom `login_view` at `/login/` (`harpoon2/views.py:17-77`). `LOGIN_URL='/login/'` and `LOGOUT_REDIRECT_URL='/login/'`. Auth model is `users.CustomUser` (extends `AbstractUser`). User-timezone middleware (`harpoon2/middleware.py`) activates each user's tz. Notification APIs require `is_superuser` (`users/views.py:78-95`).

**Caching:**
- `CachedDownloaderStatus` model updated every 10 s by `entities.tasks.cache_downloader_status` so the dashboard reads active downloads from the DB instead of hitting the downloader APIs per request.
- `django.core.cache` used by `Mylar3.poll` for last-seen log timestamps.
- `wb server-side cache` style HTTP cache is not used; the dashboard polls with `?nocache=…` strings.

**Theming:** Bootswatch theme loaded from jsDelivr CDN at `templates/base.html:16` plus a per-theme override at `static/css/overrides/{theme}.css` (one file per `THEMES` entry in `harpoon2/settings.py:257-281`). `template_content.custom_proc` exposes `interface` to every template.

---

*Architecture analysis: 2026-08-28*
