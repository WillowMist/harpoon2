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

### No Real Item/Transfer Data in Code or Commits
- **Never include actual `Item.name` or `FileTransfer.filename` values in code comments, commit messages, PR descriptions, docstrings, log strings, exception messages, test fixtures, or committed `.planning/` artifacts.** These are user library data (torrent titles, episode names, file names) and effectively PII for the operator.
- Use generic references ("the stalled transfer", "an item with 682 file transfers") or the item's `hash` (opaque identifier) when referencing a specific record.
- Past incident: a stalled-download fix accidentally included a torrent title and an internal IP in commit messages, requiring a `--force-with-lease` rewrite. Don't repeat it.
- If a name slips into a commit, amend and force-push **before the next push** — don't layer a new commit on top of the leak.

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
- **Post-processing flow**: Harpoon2 SFTP-transfers the file to a Bindery library root (e.g., `/mnt/processing/downloads/bindery/<item>/`), then calls `POST /api/v1/queue/manual-import` with `{path, bookId, format}`. Bindery's `ImportFromPath` reads from `downloadPath`, computes a formatted destination inside its library root (`<libraryRoot>/<Author>/<Title (Year)>/<file>` or `Part 001.ext` for audiobooks), and moves/copies/hardlinks the file there. In **move** mode (the default for usenet downloads — Bindery remaps usenet auto/hardlink → move per #1542 since finished usenet jobs don't seed) Bindery also `os.RemoveAll`s the source after a successful place. In **copy** / **hardlink** mode the staged file persists — set Bindery's import mode to `move` for Harpoon2-managed items so staging files don't leak. After import Bindery records the file location against the book via `book_files`, marks the download `imported`, and emits a `bookImported` webhook.

### Bindery root resolution (where the formatted file lands)

Bindery picks the destination root using a strict priority that does NOT depend on where the staged file lives:

- **Ebook** destination root (`effectiveLibraryDir`):
  1. Author's `RootFolderID` (per-author override)
  2. `library.defaultRootFolderId` setting (global default)
  3. `BINDERY_LIBRARY_DIR` env-var (final fallback)
- **Audiobook** destination root (`effectiveAudiobookDir`):
  1. Author's `AudiobookRootFolderID` (per-author override)
  2. `BINDERY_AUDIOBOOK_DIR` env-var (no global default setting fallback)

The three roots each have a role:

| Root | Bindery's role | Used by |
|---|---|---|
| `/mnt/media/Books` | ebook destination | `BINDERY_LIBRARY_DIR` (or default setting, or author's `RootFolderID`) |
| `/mnt/media/Audiobooks` | audiobook destination | `BINDERY_AUDIOBOOK_DIR` (or author's `AudiobookRootFolderID`) |
| `/mnt/processing/downloads/bindery` | staging only | Harpoon2's `manager.folder.folder`; Bindery never places here, only reads from it |

The third root must be registered as a Bindery library root so `manual-import` accepts Harpoon2's staged path (Bindery rejects paths outside any configured root with 403). Bindery will happily move files across root boundaries in move mode (cross-device moves handled via copy-then-delete).

Paths shown above are placeholders. Operator-specific paths belong in operator configuration, not in this file.

### Bindery status mapping
- `downloading` → `Grabbed`
- `downloading` (still in progress) → `Grabbed`
- `downloaded` / `importPending` → `PostProcessing`
- `imported` → `Completed`
- `failed` / `importFailed` / `importBlocked` → `Failed`
- `importFailed` / `importBlocked` whose `errorMessage` contains the configured
  `transient_error_substring` (default: "the download may still be finishing") →
  `PostProcessing` instead of `Failed`. Lets the transfer pipeline keep
  retrying when Bindery's first attempt raced Harpoon2's SFTP.

### Bindery manager JSON options (Manager.options)

Per-manager JSON config consumed by the Bindery class. Set via the manager
form's Bindery-only panel. Defaults are empty / sensible.

| Key | Purpose |
|---|---|
| `ebook_folder` | Local staging root for ebooks. If unset, falls back to `manager.folder`. |
| `audiobook_folder` | Local staging root for audiobooks. Falls back to `ebook_folder`. |
| `ebook_category` | SABnzbd / qBittorrent category for ebooks. Set on Bindery's download client; surfaced here for reference. |
| `audiobook_category` | Same, for audiobooks. |
| `path_remap` | Comma-separated `from:to` prefixes applied at the manual-import API call. Longest-prefix-first. Used to tell Bindery to look in a path it doesn't natively see (e.g. `/mnt/processing/downloads/bindery:/downloads/books`). |
| `transient_error_substring` | Override of the default Bindery transient-error hint. Default: "the download may still be finishing". |

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

## Current Debugging State (as of 2026-09-01)

**Net regression: before Phase 5 the system worked for everything except one stalled long-torrent. After Phase 5 + the 3 hot-fix patches, the UI is accessible again only because celery + gunicorn have been manually restarted with the worker pool emptied. Do not start celery back up without the dispatcher back-pressure fix from Phase 6.**

### Phase 5 outcome (delivered 2026-08-31 / 2026-09-01)

12 commits across 5 plans, verifier PASS (113 / 113 tests):

| Plan | Subject | Files |
|------|---------|-------|
| 05-01 | `feat(05-01)` bounded celery concurrency (`--concurrency=4 --prefetch-multiplier=1`) + `PIPELINE_HARDENING_ENABLED` feature flag (default true) + `Item.last_recovery_at` migration 0010 + supervisord edit | `c755342`, `d54bd97` |
| 05-02 | `feat(05-02)` `dplibs/retry.py` (`api_retry` + `sftp_retry` factories) wrapping 4 network call sites: `Arr.test()`, `Bindery.test()`, `poll_manager()`, 2× SFTP connect | `baa7a81`, `9040d51`, `8f2b81d`, `a4ec201` |
| 05-03 | `feat(05-03)` unified `_recover_one_item` state machine in `check_stalled_transfers` — Block A + Block B merged, 300s stall threshold pinned via freezegun, legacy path under flag=false | `2421eba`, `54bf4b2` |
| 05-04 | `feat(05-04)` `select_for_update(skip_locked=True)` row lock at top of `transfer_files_async` inside `transaction.atomic()` (PIPE-02 + COR-06) | `7ae08ea`, `c22de80` |
| 05-05 | `feat(05-05)` `retry_postprocessing` attempt cap (3) + deterministic `task_id` + `Item.last_recovery_at` 60s cooldown + `Item.attempt_count` migration 0011 | `ea12ba0`, `4bb1c41` |

### Phase 5 hot-fix patches (post-verifier, pushed 2026-09-01)

After Phase 5 was pushed, production showed the layered-architecture problem AGENTS.md §"If asked..." warned about — each surface patched, another surfaced.

3. **`fix(queue-ui)`** (`c2323f4`) — `templates/queue.html` was POSTing to URL paths that no longer existed after commit `16953cb` renamed them. All "Mark as Failed / Completed / Reset to Grabbed / Retry Transfer" clicks 404'd. Repointed to registered paths `/cancel/postprocessing/<hash>/` and `/retry/postprocessing/<hash>/`. With this, operators could finally move stuck items out of PostProcessing via the UI.

3. **`fix(postprocess)`** (`8c96cff`) — `postprocess_item` had no idempotency guard. `check_downloaders` fires every 20s and would re-dispatch `transfer_files_async` for already-PostProcessing items, since the function blindly transitioned status and queued the transfer. Added `if item.status != 'Grabbed': return` immediately after `Item.objects.get()`. Closes the duplicate-dispatch cascade at its source.

4. **`fix(views)`** (`3506ebc`) — `cancel_postprocessing` (action='retry') was flipping status to Grabbed but leaving `last_recovery_at`, `attempt_count`, and stale `FileTransfer` rows in place. After Phase 5 the recovery state machine would re-queue the item within 60s and `transfer_files_async` would no-op because transfers already existed. Added explicit state reset: NULL `last_recovery_at`, zero `attempt_count`, delete `FileTransfer` rows for this item.

### What's still broken (the layer Phase 5 did not close)

After deploying all three hot-fixes, the operator reset four items from PostProcessing → Grabbed. Within minutes:

- Celery `inspect active` showed four concurrent `transfer_files_async` tasks **all for the same item hash `44C87CC876D3326C62A1FCD438D5DB12D3C4EFC1`** (worker PIDs 29, 30, 31, 32 — all four `--concurrency=4` slots occupied by ONE item).
- gunicorn's three workers eventually timed out (they were blocked waiting for DB connections those four celery forks held), died, and the master process accepted new TCP connections with no workers to forward them to → UI timeouts on every page including direct-IP curl.
- SFTP never started for the four items — celery reserved the worker slots but the duplicate tasks were in a redundant loop (each task acquired the lock briefly, then released it; the next duplicate acquired it; repeat).

Root cause: **`select_for_update(skip_locked=True)` in `transfer_files_async` is released as soon as the `with transaction.atomic()` block exits at `itemqueue/tasks.py:418` (~10ms after acquire).** The lock is meant to close the duplicate-row race (which it does — FileTransfer rows are protected by `UniqueConstraint`), but it does NOT close the duplicate-work race. Four tasks for the same hash sequentially each spend an hour transferring the same files.

This is oracle's Hypothesis 2 from the 2026-09-01 incident report — confirmed in production after the three hot-fixes landed.

### Known items in known states (as of 2026-09-01 ~02:50 UTC)

- **Hash `44C87CC876D3326C62A1FCD438D5DB12D3C4EFC1`** (the long-running stalled torrent): status=Failed, 682 FileTransfer rows (~132 completed). Was the test case for the original recovery loop; now also the test case for the duplicate-work race.
- **Four Sonarr items** (hashes start with `SABnzbd_nzo_`, `EB7C482969E8`, `BEB1140657FC`): reset to Grabbed via the queue UI after `c2323f4` landed; were the items that triggered the 2026-09-01 incident. None have transfers. Celery currently STOPPED — they will NOT attempt to transfer until the Redis queue is flushed and celery is restarted.
- **Celery is currently STOPPED** (`supervisorctl stop celery-worker celery-beat`) — the operator manually stopped it to break the duplicate-dispatch cascade. UI is currently responsive because gunicorn has no DB connection competition.
- **Gunicorn was manually restarted** after its workers timed out and died (`supervisorctl restart django` from the docker host). New master pid 692, workers spawned successfully.
- **Redis queue contains 4 stale `transfer_files_async` tasks** (task ids `ba98d627-bc0c-461b-a532-22035f34d12b`, `70c57fb7-10f9-4ee5-8cdb-559b38dcb923`, `fd5f27ce-f884-4020-8d83-81cfd3b3b0a3`, `d11e5115-2b53-4760-8f3f-757296f9120f`) — all `redelivered=True`. `redis-cli` is not installed in the harpoon2 container, so the queue cannot be flushed from inside it. The harpoon2-redis container has it.

### Operational notes for the current container state

- Container name is `harpoon2` (NOT `harpoon2-app` — older AGENTS.md references were stale).
- Postgres container is `harpoon2-postgres`. Redis container is `harpoon2-redis`.
- Access from this dev machine: `ssh docker` (no credentials required), then `docker exec harpoon2 ...`.
- `supervisord.conf` runs gunicorn with `--workers 3 --threads 2 --timeout 60` (from earlier commit `21cf0c1`). Workers died once already (2026-09-01) under DB starvation; `--timeout 60` killed them; new master spawned workers successfully after restart.
- Celery worker command is `celery -A harpoon2 worker -l debug --max-tasks-per-child=10`.
- Postgres `max_connections = 100`, `CONN_MAX_AGE = 60s` (in `harpoon2/settings_template.py`).
- **Do not restart celery until the dispatcher back-pressure fix from Phase 6 is in place.** The 4 stale Redis tasks will re-fire on restart and re-saturate the worker pool.

### If asked "why does the UI keep dying / why is the recovery looping", answer:

> The recovery loop is a structural problem with multiple layers. Phase 5 closed three layers (data-layer races, retry storms, legacy recovery-block interleaving) and the three hot-fix patches closed two more (`check_downloaders` duplicate dispatch + `cancel_postprocessing` state-clear). What remains is the **dispatcher back-pressure layer**: multiple dispatch paths can each queue `transfer_files_async` for the same item hash within the same second, and the `select_for_update` lock doesn't prevent duplicate *work*, only duplicate *rows*. Phase 6 must close this with a per-hash Redis counter or a `try_acquire` semaphore, plus drain-on-idle for the celery beat scheduler so `check_downloaders` and `check_stalled_transfers` pause when the worker pool is saturated. **Do not chase symptoms with another hot-patch.**

### Phase 6: Dispatch Back-Pressure (planned — DO NOT START WITHOUT A PLAN)

Scope of Phase 6 (sequence-dependent, with rollback at each step):

1. **Per-hash transfer semaphore** — Redis `INCR` on `transfer_lock:{item_hash}` after the `select_for_update` lock; if counter > 1, the task exits cleanly. 5-minute TTL on the key. Single-line atomic guard, no schema change. Closes the duplicate-work race immediately.
2. **Drain-on-idle for celery beat** — `check_downloaders` and `check_stalled_transfers` check `celery inspect active` (or `redis LLEN celery`) before dispatching; pause if active count >= concurrency threshold. Prevents re-dispatch into a saturated pool.
3. **Single source of truth for "is this item being worked on"** — `Item.transfer_in_flight` boolean column (or a Redis `SETNX item:{hash}:working EX 7200`) replaces the implicit "status == PostProcessing" check. `postprocess_item`, `check_downloaders`, `check_stalled_transfers`, and `transfer_files_async` all read this one signal instead of inferring from status.
4. **Tenant quota for `transfer_files_async`** — cap concurrent tasks per `(downloader_id, item_size_bucket)` to prevent one large file from holding 4 worker slots. If item size > 50GB, dispatch with `--soft-time-limit=3600` and dedicated queue.
5. **Redis flush in recovery script** — `bin/recover.sh` that wraps `docker exec harpoon2-redis redis-cli FLUSHDB` + `supervisorctl restart celery-worker celery-beat` + a post-restart sanity check (active count <= concurrency).

The four items currently in `Grabbed` should NOT be touched until plan 1 is in place. Once plan 1 is in: flush Redis, restart celery, the items will pick up one-at-a-time with no duplicate-dispatch.
