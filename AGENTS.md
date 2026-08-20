# Harpoon2 Agent Guidelines

## Project Overview
Harpoon2 is a Django-based download manager that integrates with Sonarr/Radarr/Whisparr, multiple downloaders (RTorrent, SABnzbd, QBittorrent, AirDC++), and handles file transfers with post-processing.

## Key Conventions

### Authentication
- Dashboard, config pages (settings, managers, downloaders), queue, history, and search all require `@login_required` decorator
- Use Django's `get_user_model()` for user operations
- Custom login view at `/login/` that shows "Create Account" form if no superuser exists
- `LOGIN_URL = '/login/'` in settings to redirect to custom login

### URL Naming
- URL names use **underscores** (e.g., `archive_item`, `update_item_status`)
- URL paths use **slashes** (e.g., `/archive/<str:item_hash>/`)
- Always match URL names between `urls.py` and templates

### CSS/Theming
- Theme overrides live in `static/css/overrides/{theme}.css`
- Use overrides for theme-specific styling issues
- Modal styles may differ from page styles - test both

### Database
- Uses PostgreSQL in Docker, SQLite for local dev
- Uses Django ORM with `get_user_model()` for auth
- Boolean fields: use Python `True`/`False`, not SQLite integers

### Downloaders
- Downloader-specific logic lives in `entities/downloaders/{name}.py`
- Each downloader implements: `get_completed()`, `verify_completion()`, `get_download_info()`
- **Important**: Methods must be properly indented inside the class, not in wrapper functions

### Docker Workflow
1. Make code changes locally
2. Commit and push to GitHub
3. User will pull the updated image and restart the container
4. For celery workers after restart: `pkill -HUP -f 'celery.*worker'` to reload code (if needed)

### Testing in Container
- Access the running harpoon2 container with: `ssh docker` (no credentials required)
- Once inside: `docker exec harpoon2-app python manage.py shell` for Django shell
- Use this for database queries, testing code changes, and debugging

### Git Workflow
- Commit often with clear messages
- Push after each logical change
- Don't commit debug code (print statements, debug files)

## Documentation

### Using Context7 for Library Documentation
- **Always use Context7** when working with external libraries or frameworks (Django, Celery, PostgreSQL, etc.)
- Call `context7_resolve-library-id` first to get the library ID, then `context7_query-docs` for specific questions
- Context7 provides up-to-date documentation and code examples
- Only fall back to web search if Context7 doesn't have the library

### Common Tasks

### Adding a new downloader
1. Create `entities/downloaders/{name}.py` with class inheriting from `BaseDownloader`
2. Implement `get_completed()`, `verify_completion()`, `get_download_info()` methods
3. Add to `DOWNLOADER_TYPES` in settings
4. Add to downloader cache and check tasks
5. Export in `__init__.py`

### Fixing indentation bugs in downloaders
- Check that class methods are properly indented inside the class
- Use `grep -n "^def \|^class "` to find function/class definitions
- Methods incorrectly placed in wrapper functions will cause "not implemented" errors

### Adding protected pages
1. Add `@login_required` decorator to view function
2. Ensure `LOGIN_URL` setting points to custom login
3. Test: logout and verify redirect to `/login/`

## Transfer Pipeline Architecture (Managers)

The transfer pipeline in `itemqueue/tasks.py:transfer_files_async` is the **single shared path** for all manager/downloader combinations (except AirDC++). Understand it before changing anything around managers:

1. SFTP from seedbox → local `temp_folder` (per manager)
2. Extract ZIP/RAR archives in place
3. *(Blackhole only)* Move `temp_folder` → `final_folder`
4. Call `item.manager.post_process(item, download_path)` — **this is where each manager decides what "post-processing" means**
5. On success → `item.downloader.client.cleanup()` removes the seedbox copy
6. Mark item `Completed`

The `download_path` passed to `post_process` is the local path of the file/folder, not the seedbox path. Each manager's `post_process` translates that into whatever its API expects (Sonarr → `DownloadedEpisodesScan`, Radarr → `DownloadedMoviesScan`, Bindery → `manual-import`, etc.).

**AirDC++ is the documented exception** — it does its own thing because it pulls files off the seedbox itself rather than waiting for a download-client completion event.

### Per-manager import-target ID (`Item.clientid`)

The field is misnamed. Despite the name, it's not the *download client ID* — it's the **manager's reference ID for the record being imported**, retrieved at queue-poll time and reused in `post_process`.

- **Sonarr/Radarr/Lidarr/Whisparr/Readarr/LazyLibrarian**: store the queue row's `id` from the `/api/v3/queue` record. The arr `DownloadedEpisodesScan` / `DownloadedMoviesScan` payload uses this as `downloadClientID` to associate the import with the right queue row.
- **Bindery** (planned): store the Bindery `bookId`. The Bindery `manual-import` payload uses it as `bookId` to attach the staged file to the right catalogue book.

Two different identifiers from two different APIs, but the same role: "the handle this manager needs to find the record this file belongs to." Don't add a new field per manager — this field was made for this.

## Bindery Manager (planned)

Bindery (`https://github.com/vavallee/bindery`) is a Readarr replacement for ebooks/audiobooks. Single Go binary, SQLite, `/api/v1/*` + `/api/queue` (arr-compatible).

- Local instance: `http://192.168.1.77:8787`
- Key endpoints used by Harpoon2:
  - `GET /api/queue` — arr-compatible queue, `records[].downloadId` matches Harpoon2's `Item.hash`
  - `GET /api/v1/queue` — Bindery-native queue with `bookId`, `book{}` nested object, error messages
  - `GET /api/v1/downloadclient` — list, `name` matches Harpoon2's `Downloader.name`
- **Post-processing flow**: Harpoon2 SFTP-transfers the file to a Bindery library root (e.g., `/mnt/processing/downloads/bindery/<item>/`), then calls `POST /api/v1/queue/manual-import` with `{path, bookId, format}`. Bindery moves the file to the formatted path inside the same root and marks the book `imported`. The staged file disappears as part of Bindery's import.

### Bindery status mapping
- `downloading` → `Grabbed`
- `downloading` (still in progress) → `Grabbed`
- `downloaded` / `importPending` → `PostProcessing`
- `imported` → `Completed`
- `failed` / `importFailed` / `importBlocked` → `Failed`

### Bindery queue polling
- `assign_items_to_downloaders()` currently hits `/api/v3/queue` for non-Lidarr/Readarr managers — wrong for Bindery. Bindery needs `/api/queue` (the arr-compatible one).
- Match Bindery queue rows to Harpoon2 `Item`s by `downloadId == item.hash`.
- Match Bindery's `downloadClient` (e.g., "SABnzbd") to a Harpoon2 `Downloader` by name before falling back to protocol-based assignment.

## Known Issues / Lessons Learned

- **Multiple re-runs of `transfer_files_async` create duplicate `FileTransfer` records** for the same file. The dashboard then shows double file size and 50% progress. Fixed by reusing existing `completed` records and deleting stale pending/transferring/failed ones before re-creating. See commit history.
- **Race condition on container reboot**: docker-compose's `depends_on + healthcheck` lets the app container start before Postgres/Redis are actually accepting connections. `entrypoint.sh` now has `wait_for_postgres()` / `wait_for_redis()` retry loops before running migrations.
- **RTorrent `f.multicall()` returns empty for single-file torrents** in some rTorrent versions. Use `d.is_multi_file()` + `d.base_path()` instead to detect single-file torrents and get the file path.
- **Single-file handling was scoped to AirDC++ only** for a long time — generalized to all downloaders. Per-downloader `get_download_info()` should still return `files_to_copy` for single-file items, but the destination code no longer needs to know it's AirDC++.
- **Mylar3 downloader assignment**: was failing because Mylar3's API has no `/api/v3/queue`. Fixed by extracting the downloader from the log message prefix (`[AIRDCPP]`, `[SABNZBD]`, etc.) at Item creation time.
- **`unrar` is in Debian's `non-free` repo** — python:3.12-slim only has `main`. Dockerfile adds `non-free` dynamically based on `VERSION_CODENAME`.
