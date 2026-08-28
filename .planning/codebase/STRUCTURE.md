# Codebase Structure

**Analysis Date:** 2026-08-28

## Directory Layout

```
harpoon2/
├── harpoon2/                 # Django project package (settings + URL root + dashboard views)
├── entities/                 # "entities" app: Manager/Downloader/Seedbox models + adapters + poll tasks
│   └── downloaders/          # Sub-package of per-client downloader adapters (BaseDownloader + 4 clients)
├── itemqueue/                # "itemqueue" app: Item / FileTransfer / ItemHistory models + transfer tasks
├── users/                    # "users" app: CustomUser, NotificationSettings, Notification
├── templates/                # Project-level templates (base + dashboard pages + login + search results)
├── entities/templates/       # Per-app templates for the entities app (CRUD modals)
├── users/templates/          # Per-app templates for the users app
├── static/                   # Static assets: SB-Admin CSS, FontAwesome, per-theme overrides, jQuery, datatables
├── dplibs/                   # Project-specific helper libs (search view, session helpers)
├── lib/rtorrent/             # Vendored rtorrent-python 0.2.9 XML-RPC library
├── .planning/                # GSD planning artifacts (the mapping documents live here)
├── manage.py                 # Django CLI entry point
├── entrypoint.sh             # Container entrypoint (waits for DB/Redis, migrates, supervisord)
├── supervisord.conf          # Runs celery-worker + celery-beat + django
├── Dockerfile                # python:3.12-slim + unrar + supervisord
├── docker-compose.yml        # Postgres + Redis + app orchestration
├── requirements.txt          # Pinned dependency ranges (Django 5.2, celery 5.4, redis 5, paramiko, qbittorrent-api, etc.)
├── Pipfile / Pipfile.lock    # Legacy pipenv manifest (kept in sync with requirements.txt)
├── _version.py               # bump2version-managed __version__
├── .bumpversion.cfg          # bump2version config
└── *.md                      # AGENTS.md / README.md / DEPLOYMENT.md / DOCKER.md / USER_GUIDE.md / etc.
```

## Directory Purposes

**`harpoon2/`:**
- Purpose: Django project config. `settings.py` (symlink to `/data/settings.py` in container), URL root, Celery instance, dashboard views, login view, template context processor, wsgi/asgi, daily session cleanup task.
- Contains: `settings.py`, `settings_template.py`, `urls.py`, `views.py`, `celery.py`, `tasks.py`, `middleware.py`, `template_content.py`, `wsgi.py`, `asgi.py`.
- Key files:
  - `harpoon2/urls.py` — root URL routes (dashboard, queue, history, archive, cancel, retry, `/api/...`, mounts `/entities/` and `/users/`).
  - `harpoon2/views.py` — dashboard + JSON APIs (`api_dashboard`, `api_queue`, `api_history`, `api_item_history`, `api_item_transfers`, `api_version_check`) + archive/cancel/retry actions.
  - `harpoon2/celery.py` — `Celery('harpoon2')` + beat schedule.
  - `harpoon2/settings.py` — env-driven settings (Postgres/SQLite switch, Celery broker, theme list, manager/downloader type choices).
  - `harpoon2/template_content.py` — context processor injecting `brandingname`, `interface`, `ip_address`, `version`.

**`entities/`:**
- Purpose: Configuration domain. Defines `Manager`, `Downloader`, `DownloadFolder`, `Seedbox`, `CachedDownloaderStatus`; per-type adapters in `managers.py` and `downloaders/`.
- Contains: `models.py`, `managers.py`, `downloaders.py` (re-export shim), `downloaders/` package, `forms.py`, `views.py`, `urls.py`, `tasks.py`, `admin.py`, `apps.py`, `tests.py`, `migrations/`, `templates/`, `management/commands/`.
- Key files:
  - `entities/models.py` — `DownloadFolder`, `Manager` (with `from_db` → `client`), `Downloader` (with `from_db` → `client`, `DOWNLOADER_NAME_MAP` lookup), `Seedbox`, `CachedDownloaderStatus`.
  - `entities/managers.py` — `Arr` base + `Sonarr`/`Radarr`/`Lidarr`/`Readarr`/`Whisparr`, `Mylar3`, `Bindery`, `Blackhole`, `LazyLibrarian`.
  - `entities/downloaders/base.py` — `BaseDownloader` (ABC with `add`/`find`/`get_status`/`get_files`/`delete`/`test` abstract; default no-ops for `get_completed`/`verify_completion`/`get_download_info`/`cleanup`).
  - `entities/downloaders/rtorrent.py` — RTorrent via `lib/rtorrent`; `RTorrentXMLRPC` direct client + `RTorrentDownloader` adapter.
  - `entities/downloaders/sabnzbd.py` — SABnzbd HTTP API.
  - `entities/downloaders/airdcpp.py` — AirDC++ HTTP API (monitoring-only).
  - `entities/downloaders/qbittorrent.py` — `qbittorrent-api` with ban-backoff (`BAN_BACKOFF_SECONDS = 1800`).
  - `entities/tasks.py` — `poll_managers`, `poll_manager`, `poll_blackhole_managers`, `poll_blackhole_manager`, `assign_items_to_downloaders`, `cache_downloader_status`.
  - `entities/views.py` — Modal CRUD views (Create/Update/Delete for Manager/Downloader/Seedbox/DownloadFolder), `managertest`, `test_downloader`, `get_downloader_options`, `get_download_folders`.
  - `entities/forms.py` — `ManagerModalForm` (with arr/blackhole/bindery CSS hooks), `DownloaderModalForm`, `SeedboxModalForm`, `DLFolderModalForm`.
  - `entities/apps.py` — Startup hook reassigning downloaders to items stuck in `PostProcessing`.

**`itemqueue/`:**
- Purpose: Work-queue domain. Item lifecycle (Created → Grabbed → PostProcessing → Completed/Failed), per-file transfer state, immutable audit history, archive.
- Contains: `models.py`, `tasks.py`, `views.py`, `admin.py`, `apps.py`, `tests.py`, `migrations/`.
- Key files:
  - `itemqueue/models.py` — `Item` (primary key = `hash`), `ItemHistory`, `FileTransfer`.
  - `itemqueue/tasks.py` — `transfer_files_async` (central pipeline), `postprocess_item`, `check_downloaders`, `check_stalled_transfers`, `retry_postprocessing`, `check_downloader_failures`; archive extraction helpers `process_rar_archives`, `process_zip_archives`, `find_rar_archives`, `find_zip_archives`, `extract_rar_archive`, `extract_zip_archive`.
  - `itemqueue/views.py` — currently empty / placeholder.

**`users/`:**
- Purpose: Auth + user preferences + admin notifications.
- Contains: `models.py`, `forms.py`, `views.py`, `urls.py`, `admin.py`, `apps.py`, `signals.py`, `tests.py`, `migrations/`, `templates/`.
- Key files:
  - `users/models.py` — `CustomUser` (extends `AbstractUser`; adds `interface`, `prefs`, `timezone`), `NotificationSettings`, `Notification` (`create_for_admin`, `_should_notify`).
  - `users/views.py` — `userprefs` (saves prefs + notification settings), `detail`, notification JSON APIs.
  - `users/forms.py` — `CustomUserCreationForm`, `UserPrefsForm`, `NotificationSettingsForm`.
  - `users/signals.py` — `user_logged_in` handler populates session metadata.
  - `users/urls.py` — `/users/<id>/`, `/users/prefs/`, notification JSON endpoints.

**`dplibs/`:**
- Purpose: Project-specific helper libraries (not third-party). Currently provides a top-level `/search/` view and session helpers used by `users.models.CustomUser`.
- Contains: `search.py` (defines `urlpatterns` and the `search` view), `session.py` (`clear_inactive_sessions`, `get_sessions`).
- Key files: `dplibs/search.py` — used as the mounted URL include in `harpoon2/urls.py` (`from dplibs import search as search_module`); `dplibs/session.py` — used by `users.models.CustomUser.get_active_sessions`.

**`lib/rtorrent/`:**
- Purpose: Vendored `rtorrent-python` 0.2.9 (Chris Lucas, MIT) XML-RPC wrapper used by the RTorrent downloader.
- Contains: `__init__.py` (`RTorrent` class), `connection.py`, `torrent.py`, `file.py`, `peer.py`, `tracker.py`, `group.py`, `rpc/`, `lib/`.
- Key files: `lib/rtorrent/__init__.py:51-503` (`RTorrent` class with `get_torrents`, `load_torrent`, `load_magnet`).

**`templates/`:**
- Purpose: Project-level templates referenced by `harpoon2/views.py`.
- Contains: `base.html` (layout + nav + theme + Bootswatch CDN + per-theme override CSS link), `home.html`, `queue.html`, `history.html`, `search_results.html`, `registration/login.html`.
- Key files: `templates/base.html` is the canonical layout; uses SB-Admin markup, FontAwesome, jQuery UI, DataTables (CDN). All dashboards extend this base.

**`entities/templates/entities/` and `users/templates/users/`:**
- Purpose: App-scoped templates. Modal forms (`*create.html`, `*delete.html`) plus listing pages (`managers.html`, `downloaders.html`, `settings.html`) and the user profile (`detail.html`, `prefs.html`).
- Contains: CRUD modals that pair with `entities/forms.py` and `crisp_modals.views.ModalCreateView` etc.

**`static/`:**
- Purpose: Static assets served by WhiteNoise.
- Contains: `css/` (`sb-admin.css`, `jquery-ui.css`, `attachment.css`, `overrides/{theme}.css` per Bootswatch variant), `js/` (`sb-admin.js`, `datatableview.js`, `jquery-ui.js`, `test-downloader.js`, etc.), `fa/` (FontAwesome 6), `images/` (favicon, logo, apple-touch-icon), `crisp_modals/`.
- Key files: `static/css/overrides/{theme}.css` for per-theme fixes; `static/js/test-downloader.js` for the live "Test connection" button on the downloader modal.

**`.planning/`:**
- Purpose: GSD planning artifacts (ROADMAP, phases, codebase mapping).
- Contains: `codebase/` (where this document and the matching `ARCHITECTURE.md` live).
- Key files: `.planning/codebase/STRUCTURE.md` (this document), `.planning/codebase/ARCHITECTURE.md`.

## Key File Locations

**Entry Points:**
- `manage.py` — Django CLI.
- `harpoon2/wsgi.py` — WSGI callable.
- `harpoon2/asgi.py` — ASGI callable.
- `harpoon2/urls.py` — root URL configuration.
- `harpoon2/celery.py` — Celery app + beat schedule.
- `entrypoint.sh` — container entrypoint.

**Configuration:**
- `harpoon2/settings.py` — runtime settings (symlinked to `/data/settings.py` in container).
- `harpoon2/settings_template.py` — template copied into `/data/settings.py` at image build (`Dockerfile:24`).
- `_version.py` — current version (`2.1.3` per `.bumpversion.cfg`).
- `.env.example` — environment variables consumed by `settings.py` (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `USE_POSTGRES`, `DB_*`, `REDIS_*`).
- `requirements.txt` / `Pipfile` — dependency manifests.
- `Dockerfile` — image build (python:3.12-slim, non-free apt repo for `unrar`, supervisord).
- `docker-compose.yml` / `docker-compose.example.yml` — local stack (Postgres, Redis, app).
- `supervisord.conf` — process supervision inside the container.

**Core Logic:**
- `entities/managers.py` — manager adapters and `post_process` dispatch.
- `entities/downloaders/{base,rtorrent,sabnzbd,airdcpp,qbittorrent}.py` — downloader adapters.
- `entities/tasks.py` — manager / blackhole / assign / cache tasks.
- `itemqueue/tasks.py` — `transfer_files_async` (single shared pipeline) + extraction helpers.
- `itemqueue/models.py` — `Item`, `FileTransfer`, `ItemHistory`.

**Templates:**
- `templates/base.html` — layout used by every dashboard page.
- `templates/home.html`, `templates/queue.html`, `templates/history.html`, `templates/search_results.html`, `templates/registration/login.html`.
- `entities/templates/entities/*.html` — modal create/update/delete + listing pages.
- `users/templates/users/{detail,prefs}.html`.

**Testing:**
- `entities/tests.py` — placeholder `TestCase`.
- `itemqueue/tests.py` — placeholder `TestCase`.
- `users/tests.py` — placeholder.

(No tests are currently implemented; see CONCERNS for context.)

## Naming Conventions

**Files:**
- Django project files: lowercased (`settings.py`, `celery.py`, `urls.py`).
- Django app files: lowercased (`models.py`, `forms.py`, `views.py`, `urls.py`, `admin.py`, `apps.py`, `tasks.py`, `tests.py`, `signals.py`, `tasks.py`).
- Migration files: Django-generated numeric prefix (`0001_initial.py`, `0018_manager_bindery_fields.py`).
- Adapter modules use the *display* name of the type, not the Python identifier: `entities/downloaders/airdcpp.py` for `AirDCpp`, `entities/downloaders/sabnzbd.py` for `SABnzbd`. Display-name ↔ attribute lookup happens in `entities/downloaders/__init__.py:DOWNLOADER_NAME_MAP`.
- Templates pair with one Django app per directory (`entities/templates/entities/*.html`, `users/templates/users/*.html`); the project-level `templates/` holds cross-app pages.

**Functions / classes:**
- Class-based views: `<Model><Action>View` (`ManagerCreateView`, `DownloaderUpdateView`).
- Tasks: `poll_<scope>`, `check_<scope>`, `cache_<scope>`, `<verb>_<noun>_async` (`transfer_files_async`).
- Adapter functions are PascalCase aliases at module bottom for back-compat (`SABNzbd`, `RTorrent`, `AirDCpp`, `QBittorrent`) wrapping the proper class so legacy `getattr(downloaders, 'AirDC++')` lookups still work (`entities/downloaders/*.py`).

**Variables:**
- `Item.hash` is the primary key; `Item.clientid` is the *manager's reference id* (queue row id or Bindery bookId), NOT the download client id.
- Status strings: capitalized (`Created`, `Grabbed`, `PostProcessing`, `Completed`, `Failed`).
- Transfer status strings: lowercase (`pending`, `transferring`, `completed`, `failed`).

**Types:**
- Manager types: human-friendly strings (`'Sonarr'`, `'Radarr'`, `'Lidarr'`, `'Readarr'`, `'Whisparr'`, `'LazyLibrarian'`, `'Mylar3'`, `'Bindery'`, `'Blackhole'`) — see `MANAGER_TYPES` in `harpoon2/settings.py:283-293`.
- Downloader types: same style (`'RTorrent'`, `'SABNzbd'`, `'AirDC++'`, `'QBittorrent'`) — see `DOWNLOADER_TYPES` in `harpoon2/settings.py:295-300`. Note the `AirDC++` display name is mapped to `AirDCpp` for Python attribute lookup.

## Where to Add New Code

**New Manager type (e.g., another arr variant):**
- Adapter: add a class in `entities/managers.py` (subclass `Arr` if arr-compatible, otherwise follow the `Bindery` / `Mylar3` shape: `__init__(manager)` + `check_queue` + `post_process` + `test` + optional `poll`).
- Register the type: append to `MANAGER_TYPES` in `harpoon2/settings.py:283-293`.
- Dispatch: if the poll path is non-standard, branch in `entities.tasks.poll_manager` (`entities/tasks.py:21-220`) and/or `entities.tasks.poll_managers` (`entities/tasks.py:14-18`).
- Form fields: if the manager needs custom fields, add them as dedicated columns on `Manager` (see `bindery_*` fields at `entities/models.py:34-57`) and tag them in `entities/forms.py:ManagerModalForm.__init__` (`arr_only_fields` / `blackhole_only_fields` / `bindery_only_fields` sets) so the form can toggle visibility per `managertype`.
- Migration: `python manage.py makemigrations entities`.

**New Downloader type:**
- Adapter: create `entities/downloaders/<lowercase>.py` defining `<Name>Downloader(BaseDownloader)` with `optionfields` dict and the five abstract methods. Add the legacy alias function at the bottom (`def <Name>(downloader=None): return <Name>Downloader(downloader)`).
- Register in package: add the import + `__all__` entry + `DOWNLOADER_NAME_MAP` row in `entities/downloaders/__init__.py`.
- Register the type: append to `DOWNLOADER_TYPES` in `harpoon2/settings.py:295-300`.
- Cache key: extend `entities/tasks.cache_downloader_status` if the new client needs a non-default branch (`entities/tasks.py:476-537`).
- Migration: `python manage.py makemigrations entities`.

**New post-processing helper (e.g., a new archive format):**
- Add helpers in `itemqueue/tasks.py` (e.g., `find_<fmt>_archives`, `extract_<fmt>_archive`, `process_<fmt>_archives`).
- Call from `itemqueue.tasks.transfer_files_async` immediately after the ZIP/RAR steps (`itemqueue/tasks.py:862-878`).
- Update `item.extraction_status` / `extraction_progress` consistently with existing patterns.

**New Celery beat task:**
- Define `@shared_task` in `entities/tasks.py` (entities-side) or `itemqueue/tasks.py` (transfer-side).
- Register the schedule in `harpoon2/celery.py:app.conf.beat_schedule` (`harpoon2/celery.py:29-69`).
- For long-running work, set `time_limit` / `soft_time_limit` (see `transfer_files_async` at `itemqueue/tasks.py:292`).

**New dashboard page or JSON endpoint:**
- Add the view in `harpoon2/views.py` (or the relevant app's `views.py`).
- Wire URL in `harpoon2/urls.py:7-36` (root) or `entities/urls.py:4-25` / `users/urls.py:7-13`.
- Decorate with `@login_required` for HTML pages; gate JSON notification endpoints on `is_superuser` (see `users/views.py:78-95`).
- Add a template under `templates/` (project) or `<app>/templates/<app>/` (app).

**New user-facing notification type:**
- Add a boolean field on `NotificationSettings` (`users/models.py:40-74`).
- Add the corresponding entry in `NotificationSettingsForm` (`users/forms.py`) and `users/views.py:userprefs` checkbox list (`users/views.py:31-46`).
- Map the type string to the field in `Notification._should_notify` (`users/models.py:115-136`).

**New static asset (CSS / JS / image):**
- Place under `static/css/`, `static/js/`, or `static/images/`. Per-theme CSS goes in `static/css/overrides/{theme}.css` to avoid the project-wide `sb-admin.css`.
- Reference via `{% static 'path/from/static' %}`.
- Production: `collectstatic` reads `STATICFILES_DIRS = [BASE_DIR / 'static']` and writes to `STATIC_ROOT = '/data/static/'` (`harpoon2/settings.py:182-187`).

## Special Directories

**`.planning/`:**
- Purpose: GSD planning artifacts.
- Contains: `codebase/` (the mapping documents), `phases/`, `milestones/`, etc.
- Generated: No (committed source).
- Committed: Yes (see `.gitignore`).

**`lib/rtorrent/`:**
- Purpose: Vendored third-party library (rtorrent-python 0.2.9).
- Generated: No.
- Committed: Yes (vendored rather than pip-installed; declared in `Pipfile` but the actual code is in-tree).

**`static/`:**
- Purpose: WhiteNoise-served static assets.
- Generated: No (manually curated; only `STATIC_ROOT` is generated by `collectstatic`).
- Committed: Yes.

**`dplibs/`:**
- Purpose: Project-specific helper libraries (NOT third-party — do not vendor here).
- Generated: No.
- Committed: Yes.

**`migrations/` (one per app):**
- Purpose: Django auto-generated schema migrations.
- Generated: Yes (via `python manage.py makemigrations`).
- Committed: Yes.

**`__pycache__/` (recursively):**
- Purpose: Python bytecode cache.
- Generated: Yes.
- Committed: No (gitignored).

---

*Structure analysis: 2026-08-28*
