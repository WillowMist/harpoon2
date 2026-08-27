# Harpoon 2 — User Guide

Everything you need to install Harpoon 2, configure it against your managers
and download clients, and understand how the transfer pipeline behaves.

For project conventions and contributor notes, see [AGENTS.md](AGENTS.md). For
production deployment (systemd, Nginx, SSL, OAuth), see
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## Table of contents

1. [Installation](#installation)
   1. [Docker (recommended)](#docker-recommended)
   2. [Bare metal / local development](#bare-metal--local-development)
2. [Concepts](#concepts)
3. [Web UI overview](#web-ui-overview)
4. [Seedboxes](#seedboxes)
5. [Download folders](#download-folders)
6. [Downloaders](#downloaders)
7. [Managers](#managers)
   1. [Manager types](#manager-types)
   2. [Common (arr-style) manager fields](#common-arr-style-manager-fields)
   3. [Blackhole manager](#blackhole-manager)
   4. [Bindery manager](#bindery-manager)
8. [Transfer pipeline](#transfer-pipeline)
9. [Bindery deep dive](#bindery-deep-dive)
10. [Troubleshooting](#troubleshooting)

---

## Installation

### Docker (recommended)

```bash
git clone https://github.com/WillowMist/harpoon2
cd harpoon2

cp docker-compose.example.yml docker-compose.yml
cp .env.example .env
# Edit .env: SECRET_KEY, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, POSTGRES_PASSWORD, etc.

docker compose up -d
```

`docker-compose.example.yml` starts three services:

- **harpoon2** — the Django app + gunicorn on port `4277`, plus an embedded
  celery worker and celery beat (supervised by `supervisord`).
- **harpoon2-postgres** — Postgres database.
- **harpoon2-redis** — Redis broker for celery.

The entrypoint waits for Postgres and Redis to be reachable before running
migrations (avoids the boot race documented in
[AGENTS.md → Known issues](AGENTS.md#known-issues--lessons-learned)).

To run without Postgres (SQLite dev fallback), set `USE_POSTGRES=false` in `.env`.

Access the UI at `http://<your-host>:4277`. The first request redirects to
`/login/`; if no superuser exists yet, that page shows a "Create Account" form
instead of the standard login form.

#### Updating

```bash
git pull
docker compose build
docker compose up -d
```

For code-only changes (no new migrations) you can sometimes skip `build` and
just reload celery workers in-place — see
[AGENTS.md → Docker workflow](AGENTS.md#docker-workflow).

### Bare metal / local development

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp harpoon2/settings_template.py harpoon2/settings.py
# Edit harpoon2/settings.py: SECRET_KEY, ALLOWED_HOSTS, DB, etc.

python manage.py migrate
python manage.py createsuperuser
```

Then in four terminals:

```bash
python manage.py runserver 0.0.0.0:4277
celery -A harpoon2 worker -l info
celery -A harpoon2 beat -l info
redis-server   # or run a Redis container
```

[QUICKSTART.md](QUICKSTART.md) walks through this same flow.

---

## Concepts

- **Manager** — a media manager (Sonarr, Radarr, Bindery, …) or "blackhole"
  watchdog that tells Harpoon2 what to grab and what to do with completed
  downloads. Harpoon2 polls the manager's queue and triggers transfers.
- **Downloader** — a download client (rTorrent, SABnzbd, qBittorrent, AirDC++)
  that Harpoon2 polls for completed downloads and pulls files from via SFTP.
- **Seedbox** — the remote host that runs your downloader. Harpoon2 SFTPs in
  to grab finished files.
- **Download folder** — a local directory that paired `manager` + `downloader`
  combinations transfer into (the manager's "final folder" on Harpoon2's side).
- **Item** — a single piece of media being processed. Has a `status` that flows
  through: `Created` → `Grabbed` → `PostProcessing` → `Completed` (or `Failed`).
- **FileTransfer** — record of a single file's SFTP transfer (pending /
  transferring / completed / failed).

A `manager` always has one (optional) `folder`, and downloads for it are
picked up by whichever `downloader` matches the download-client name the
manager reports.

---

## Web UI overview

| Path | What it does |
|---|---|
| `/` | Dashboard (active transfers, status counts, recent activity) |
| `/queue/` | Items currently grabbed / downloading / transferring |
| `/history/` | Completed and failed items; toggle "show archived" |
| `/search/` | Search across queue + history |
| `/login/`, `/logout/` | Auth |
| `/entities/settings/` | Combined settings page (folders + seedboxes) |
| `/entities/managers/` | Manager list / create / edit / delete / test |
| `/entities/downloaders/` | Downloader list / create / edit / delete |
| `/entities/seedboxes/` | Seedbox create / edit / delete (via settings page) |
| `/entities/folders/` | Download-folder create / edit / delete (via settings page) |
| `/admin/` | Django admin (DB CRUD) |

JSON endpoints (all `GET` unless noted) — used by the AJAX dashboard:

- `/api/dashboard/`, `/api/queue/`, `/api/history/`
- `/api/item/<hash>/history/`, `/api/item/<hash>/transfers/`
- `/api/version/`
- `/entities/api/downloader-options/<downloader_type>/` — dynamic form
- `/entities/api/test-downloader/<downloader_id>/` — POST: test connection
- `/entities/api/download-folders/`

Action endpoints (POST):

- `/cancel/download/<hash>/`, `/cancel/transfer/<name>/`,
  `/cancel/postprocessing/<hash>/`
- `/retry/postprocessing/<hash>/`, `/retry/failed/<hash>/`
- `/archive/<hash>/`, `/unarchive/<hash>/`,
  `/archive/all/failed/`, `/archive/all/completed/`, `/archive/clear/`
- `/update/item/status/<hash>/`, `/update/item/downloader/<hash>/`

The main app uses **underscored** URL names (e.g. `archive_item`,
`update_item_status`); the entities app uses **hyphenated** URL names
(e.g. `entities:manager-update`, `entities:dlfolder-delete`). Match the
convention when writing internal links.

---

## Seedboxes

A seedbox is a remote host that runs a download client. Harpoon2 connects over
SFTP to retrieve completed files.

### Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | str (≤30) | yes | Display name; unique |
| `host` | str (≤255) | yes | Hostname or IP |
| `port` | int | yes | SSH port (default 22) |
| `username` | str (≤100) | yes | SSH username |
| `auth_type` | `password` \| `key` | yes | `password` → paramiko password auth; `key` → paramiko `RSAKey.from_private_key_string()` |
| `password` | str (≤255) | one of | SSH password; rendered as `PasswordInput`; cleared on edit, preserved if left blank |
| `ssh_key` | text | one of | Private key contents (for `key` auth); same empty-on-edit preservation |
| `base_download_folder` | str (≤400) | no | Base path on the seedbox (e.g. `/home/user/downloads`); used by AirDC++ path resolution |

### Notes

- `password` / `ssh_key` are stored in the database in plain text. Treat the
  Harpoon2 DB as sensitive.
- Editing a seedbox leaves `password` / `ssh_key` untouched if you don't type a
  new value (deliberate — re-entering is a footgun).
- Set `auth_type = "key"` and paste your private key in `ssh_key` to use
  key-based auth.

---

## Download folders

A `DownloadFolder` is a local filesystem path the transfer pipeline writes
into (the "final" destination on Harpoon2's side).

### Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `folder` | str (≤400) | yes (unique) | Absolute local path. The form auto-creates the directory if it doesn't exist |
| `remote_folder_name` | str (≤400) | no | Optional API-facing path sent to managers in `post_process` as `base_remote_path`; falls back to `folder` if unset |

### How folders are used

A `Manager.folder` FK points at one of these. When `transfer_files_async`
runs:

1. It SFTPs the completed files into the **staging** location (`/tmp`
   default, or `manager.folder` for Blackhole).
2. Blackhole-only: moves `temp_folder` → `manager.folder`.
3. Calls `manager.post_process(item, download_path)` where `download_path` is
   built as `manager.folder.remote_folder_name` (or `.folder`) + sanitized
   item name.

For Bindery, `bindery_ebook_folder` / `bindery_audiobook_folder` override
the folder used for the manual-import — see
[Bindery manager](#bindery-manager).

---

## Downloaders

A `Downloader` is a download client. Harpoon2 polls it for completed
downloads and SFTPs the files from its seedbox.

### Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | str (≤30) | yes (unique) | Display name; matched against the `downloadClient`/`name` returned by manager queues |
| `downloadertype` | choice | yes | See [Downloader types](#downloader-types) |
| `options` | JSON | no | Type-specific options (host, port, credentials, etc.). The form serializes this as a hidden JSON field |
| `seedbox` | FK(Seedbox) | no | The seedbox this downloader pulls from |

### Downloader types

| `downloadertype` | Class | Notes |
|---|---|---|
| `RTorrent` | `RTorrent` | rTorrent XML-RPC (ruTorrent). Single-file torrents: `f.multicall()` returns empty on some rTorrent versions — handled via `d.is_multi_file()` + `d.base_path()`. |
| `SABNzbd` | `SABNzbd` | SABnzbd usenet via `/api`. The only downloader with a real `cleanup()` (calls SABnzbd's history-remove endpoint). |
| `QBittorrent` | `QBittorrent` | qBittorrent via the `qbittorrentapi` lib. Has a 30-minute IP-ban backoff so failed logins don't hammer a banned client. |
| `AirDC++` | `AirDCpp` | **Monitoring only** — no `add()`. Polls `/events` for "has finished downloading". AirDC++ pulls files off the seedbox itself, so it bypasses the shared transfer pipeline. |

`AirDC++` is the **only** downloader that doesn't use the shared transfer
pipeline — see [Transfer pipeline](#transfer-pipeline).

### Type-specific options

The form dynamically shows type-specific fields via
`/entities/api/downloader-options/<type>/`. Common keys:

- **RTorrent**: `host`, `port`, `url_path`, `use_ssl`, `username`, `password`, `startonload`
- **SABnzbd**: `url`, `apikey`, `cleanup`, `enabled`
- **QBittorrent**: `host`, `port`, `username`, `password`, `use_ssl`
- **AirDC++**: `host`, `port`, `username`, `password`, `use_https`, `target_folder`, `polling_history`

`Downloader.checkoptions()` back-fills missing keys with sane defaults on load.

### Cleaning up the seedbox

`downloader.client.cleanup(first_transfer)` is called after a successful
post-processing. SABnzbd actually removes the entry from SABnzbd's history;
RTorrent/QBittorrent/AirDC++ return `(True, "Cleanup not implemented")`
— you handle torrent/nzb cleanup out-of-band on those clients.

---

## Managers

A `Manager` tells Harpoon2 what to grab and what to do with completed files.

### Common (arr-style) manager fields

These fields apply to all manager types. The form groups them as "arr-only" for
Sonarr/Radarr/Lidarr/Readarr/Whisparr/Mylar3/Bindery.

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | str (≤30) | yes (unique) | Display name |
| `managertype` | choice | yes | See [Manager types](#manager-types) |
| `url` | URL | no | Base URL of the manager (e.g. `http://192.168.1.77:8787`) |
| `apikey` | str (≤100) | no | API key sent as `X-Api-Key` |
| `folder` | FK(DownloadFolder) | no | Local final-folder; used as staging-root fallback for Bindery |
| `label` | str (≤25) | no | Short label passed to the manager's `client` object |
| `options` | JSON | no | Legacy per-manager config; **excluded from the form** (archival only) |

The form switches the visible field set based on `managertype`:

- **arr / Bindery / Mylar3** → `url`, `apikey`, `label` (plus the Bindery panel
  for `Bindery`)
- **Blackhole** → `url`/`apikey`/`label` are hidden; the Blackhole panel is
  shown instead

### Manager types

| `managertype` | Integrates with | Notes |
|---|---|---|
| `Sonarr` | Sonarr (TV) | API at `{url}/api/v3`. Post-process sends `DownloadedEpisodesScan` |
| `Radarr` | Radarr (movies) | API at `{url}/api/v3`. Post-process sends `DownloadedMoviesScan` |
| `Lidarr` | Lidarr (music) | API at `{url}/api/v1`. Post-process sends `DownloadedAlbumsScan` |
| `Readarr` | Readarr (books) | API at `{url}/api/v1`. Post-process sends `DownloadedBooksScan` |
| `Whisparr` | Whisparr (adult) | API at `{url}/api/v3`. Post-process sends `DownloadedEpisodesScan` |
| `Mylar3` | Mylar3 (comics) | API at `{url}{http_root}/api?apikey=...&cmd=...`. Post-process calls `forceProcess` |
| `Bindery` | [Bindery](https://github.com/vavallee/bindery) (ebooks/audiobooks) | API at `{url}/api/v1` (native) + `{url}/api/queue` (arr-compatible). See [Bindery deep dive](#bindery-deep-dive) |
| `Blackhole` | None — local directory watcher | See [Blackhole manager](#blackhole-manager) |

> **Heads-up:** `LazyLibrarian` appears in the manager-type dropdown but has
> no implementation in `entities/managers.py`. Don't select it — `Manager.test()`
> and `from_db()` will throw `AttributeError`. Use `Readarr` for ebooks or
> `Bindery` instead.

### Blackhole manager

Blackhole is special: instead of polling a media manager for grabs, it watches
a local directory for `.torrent` and `.nzb` files and hands each one to a
configured downloader.

| Field | Type | Required | Notes |
|---|---|---|---|
| `monitor_directory` | str (≤500) | no | Directory to monitor |
| `monitor_subdirectories` | bool | no | If true, each subdirectory's name is used as the category |
| `category` | str (≤50) | no | Category to assign when not monitoring subdirectories |
| `torrent_downloader` | FK(Downloader) | no | Torrent client for `.torrent` files |
| `nzb_downloader` | FK(Downloader) | no | NZB client for `.nzb` files |
| `temp_folder` | str (≤500) | no | Where files land before the move into the final folder |
| `poll_interval` | int (default 60) | no | Seconds between directory scans |
| `move_on_complete` | bool (default true) | no | Move files to destination (vs copy) |
| `delete_source` | bool (default true) | no | Delete the `.nzb`/`.torrent` file after sending it to the downloader |
| `duplicate_handling` | `skip` \| `rename` \| `overwrite` (default `skip`) | no | What to do when a destination file already exists |
| `enabled` | bool (default true) | no | Master enable switch |
| `scan_on_startup` | bool (default true) | no | Process any existing files in the monitor dir on startup |

### Bindery manager

Bindery-specific fields (shown in the Bindery-only form panel):

| Field | Type | Required | Notes |
|---|---|---|---|
| `bindery_ebook_folder` | str (≤500) | no | Local staging root for ebooks. Falls back to `manager.folder` if unset |
| `bindery_audiobook_folder` | str (≤500) | no | Local staging root for audiobooks. Falls back to `bindery_ebook_folder` |
| `bindery_ebook_category` | str (≤100) | no | SABnzbd / qBittorrent category for ebooks. Set on the Bindery download client; surfaced for reference |
| `bindery_audiobook_category` | str (≤100) | no | Same, for audiobooks |
| `bindery_path_remap` | str (≤500) | no | `from:to,from2:to2` — applied at the manual-import API call. Longest-prefix-first. Use this to point Bindery at a Harpoon2 path it doesn't natively see |
| `bindery_transient_error_substring` | str (≤255) | no | If Bindery's `errorMessage` contains this substring, the item is treated as `PostProcessing` (retryable) instead of `Failed`. Default: `"the download may still be finishing"` |

Bindery's recovery flow is more involved than the other managers — see
[Bindery deep dive](#bindery-deep-dive).

---

## Transfer pipeline

All manager/downloader combinations share one transfer path (except AirDC++,
which does its own thing). The path lives in
`itemqueue/tasks.py:transfer_files_async`.

1. **Verify completion** (`postprocess_item`) — the downloader's
   `verify_completion()` confirms the file is fully downloaded on the seedbox.
   Item → `PostProcessing`.
2. **Connect** to the seedbox via SFTP (password or key auth, per the
   Seedbox).
3. **Get download info** — `downloader.client.get_download_info(hash)` returns
   `remote_dir`, `files_to_copy`, `is_single_file`.
4. **Build the file list** — single-file optimization for rTorrent; recursive
   `walk_remote_sftp` for multi-file directories; skips hidden / `.jpg` /
   `.html` artifacts.
5. **Create `FileTransfer` records upfront** — existing `completed` records
   with matching size are reused; stale pending/transferring/failed records
   are deleted. Skipped-but-complete local files now also create a
   `completed` record (so manager post-processing can find the staged path
   even when no real transfer happens).
6. **SFTP each file** with progress callback, up to 3 retries with reconnect.
7. **Extract** ZIP archives then RAR archives in place.
8. **Blackhole only**: move `temp_folder` → `final_folder`.
9. **Call `manager.post_process(item, download_path)`** — `download_path` is
   `remote_folder_name + sanitized_name` (or just the folder for single-file
   items). This is where each manager decides what to do — see the per-type
   notes in [Managers](#managers).
10. **On success**: `item.downloader.client.cleanup(first_transfer)` removes
    the seedbox copy. Item → `Completed`.
11. **On failure**: schedule `retry_postprocessing` in 5 min (and 10 min
    after that, ad infinitum). For Bindery, the item stays in `PostProcessing`
    rather than being forced to `Completed` — see [Bindery deep dive](#bindery-deep-dive).

### Other transfer-related tasks

- `check_downloaders` — polls each downloader's `get_completed()`. AirDC++
  takes a different path; for the rest it queues `postprocess_item`.
- `check_stalled_transfers` — fails transfers with no progress for 5+ min;
  resets PostProcessing items with stale failed/pending transfers to
  `Grabbed`; requeues transfers for PostProcessing items with none; re-runs
  extraction + manager post-process for all-completed items (guarded by a
  10-minute "recent post-processing" history check to avoid the 20s re-run
  loop).
- `retry_postprocessing` — re-runs manager `post_process` using the latest
  completed transfer's local folder; reschedules itself every 10 min on
  failure; skips `Grabbed`/`Archived`/`Deleted`.
- `check_downloader_failures` — SABnzbd-only; scans history for failed slots,
  matches items by name, marks them `Failed`, calls
  `manager_client.reject_download()` (`POST /queue/bulk` with `blacklist:
  true`) so the arr can search for an alternative.

---

## Bindery deep dive

Bindery (`https://github.com/vavallee/bindery`) is a Readarr replacement for
ebooks and audiobooks — single Go binary, SQLite, with an arr-compatible
`/api/queue` plus a native `/api/v1/queue`.

The Bindery integration is the most complex manager in Harpoon2 because
Bindery's auto-import can race with Harpoon2's SFTP transfer. The recovery
flow has to be careful to avoid:

- Pointing Bindery at a file that hasn't finished transferring yet
- Sending the same book into the library twice from concurrent recovery runs
- Dragging a healthy item back to `Failed` because a stale row lingers in
  Bindery's queue

### Status mapping

| Bindery status | Harpoon2 `Item.status` |
|---|---|
| `downloading` | `Grabbed` |
| `downloaded`, `importPending`, `importing` | `PostProcessing` |
| `imported` | `Completed` |
| `failed`, `importFailed`, `importBlocked` | `Failed` |

If Bindery's `errorMessage` contains `bindery_transient_error_substring`
(default `"the download may still be finishing"`), an `importFailed` /
`importBlocked` row is treated as `PostProcessing` instead — the pipeline
keeps retrying instead of marking the item permanently failed.

### Book-level recovery

The Bindery poll precomputes the set of `bookId`s that already have an
`imported` row anywhere in the queue. Any `importFailed` / `importBlocked`
row whose book is in that set is treated as `Completed` instead of `Failed`
— otherwise a stale failure row would keep flipping the item back to Failed
forever.

### Manual-import recovery flow

For items that Bindery's auto-import couldn't place (the row is in
`importFailed` or `importBlocked`), Harpoon2's `post_process` does this:

1. **Resolve the staged path** from the item's `FileTransfer` records:
   - Single completed transfer pointing at a file → the file itself
   - Otherwise → the folder containing the files (so cover art, multiple
     book files, etc. all move together)
2. **Detect format** (`ebook` vs `audiobook`) from the file extension
   (audiobook: `.m4b .mp3 .m4a .flac .ogg .aac`).
3. **Move the staged file/folder** into the matching `bindery_*_folder` if
   set (preserving the relative path inside the staging root). This makes
   Bindery's root resolution pick the right library.
4. **Apply `bindery_path_remap`** (longest-prefix-first `from:to` transform)
   so the path sent to Bindery matches Bindery's own filesystem namespace.
5. **Look up the matching Bindery queue row** by `torrentId`/`sabnzbdNzoId`:
   - No row → if the book has any in-flight import, return early success;
     otherwise create one via manual-import
   - Row `imported` → return early success (Bindery already has it)
   - Row in `importing` / `importpending` / `failed` / etc. → return early
     success (Bindery owns this row)
   - Row recoverable (`importFailed` / `importBlocked`):
     - If the book already has any in-flight/imported row → delete the
       stale original via `DELETE /api/v1/queue/{id}?removeFromClient=false`
       (keeps the torrent seeding) and return success
     - Otherwise POST `/api/v1/queue/manual-import/match` `{downloadId, bookId}`
       (200/201/202 OK; 409 is non-fatal — Bindery already retried), then
       POST `/api/v1/queue/manual-import` `{path, bookId, format}`, then
       delete the stale original row

### Bindery root resolution

Bindery picks the destination root using a strict priority that does **not**
depend on where the staged file lives:

- **Ebook** destination (`effectiveLibraryDir`):
  1. Author's `RootFolderID` (per-author override)
  2. `library.defaultRootFolderId` setting (global default)
  3. `BINDERY_LIBRARY_DIR` env-var (final fallback)
- **Audiobook** destination (`effectiveAudiobookDir`):
  1. Author's `AudiobookRootFolderID` (per-author override)
  2. `BINDERY_AUDIOBOOK_DIR` env-var (no global default setting fallback)

Harpoon2's staging folder must be registered as a Bindery library root, or
Bindery will 403 the manual-import. Bindery will happily move files across
root boundaries in move mode (cross-device moves handled via copy-then-delete).

### Setting up Bindery with Harpoon2

1. **Install Bindery** — single Go binary, SQLite, point it at your library
   roots (the `/mnt/media/Books` and `/mnt/media/Audiobooks` directories in
   most setups).
2. **Register Harpoon2's staging root as a Bindery library root** — typically
   `/mnt/processing/downloads/bindery`. Bindery will read from here during
   manual-import but never write here.
3. **Pick a path remap** — if Harpoon2 and Bindery see the staging folder via
   different paths, set `bindery_path_remap` accordingly (e.g.
   `/mnt/twilightsparkle/processing/downloads/bindery:/downloads`).
4. **Set Bindery's import mode to `move`** for Harpoon2-managed items — Bindery
   will then `os.RemoveAll` the staging file after a successful place so files
   don't leak into the staging directory.
5. **Add the Bindery manager** in Harpoon2:
   - URL: `http://<bindery-host>:<port>`
   - API key: from Bindery's settings
   - Set `bindery_ebook_folder` and `bindery_audiobook_folder` if you want
     Harpoon2 to stage books into a different root than the one Bindery uses
6. **Add the Bindery download client inside Bindery** — point it at your
   SABnzbd / qBittorrent, set categories matching
   `bindery_ebook_category` / `bindery_audiobook_category`.

---

## Troubleshooting

### Item stuck cycling between `Completed` and `Failed`

For Bindery items, this means Bindery's queue has both an `imported` row and a
stale `importFailed` row for the same book. The book-level override should
keep it `Completed`, but if you see it flipping:

1. Check Bindery's queue: `GET /api/v1/queue?pageSize=100` (with `X-Api-Key`).
   Look for the bookId — is there an `imported` row?
2. If the book's imported but the row hasn't been pruned yet, the next
   `post_process` run on the item will delete the stale row.
3. If you're stuck: `Item.objects.filter(hash=...).update(status='Completed')`
   in the Django shell to force it.

### Bindery manual-import returns 400 "path is not a recognised book file"

The staged path points at a single file that Bindery doesn't recognize as a
book (e.g. `cover.PNG`). Cause: a previous run moved only one file out of a
multi-file folder. Fix: re-run `transfer_files_async` on the item, which will
now stage the whole folder (the multi-file staging fix landed in commit
`1219dd2`).

### Bindery manual-import returns 403

Bindery rejected the path because it's outside any configured library root.
Either register Harpoon2's staging root as a Bindery root, or set
`bindery_path_remap` so the remapped path falls inside a known Bindery root.

### ItemHistory.details too long (Postgres `value too long for type character varying(500)`)

Shouldn't happen with current code — the Bindery manual-import history
details are truncated to 450 chars before insert. If you see this on another
manager, check the `details` field length in `itemqueue/models.py:ItemHistory`.

### `Item.clientid` mismatch

`Item.clientid` is the manager's reference ID for the record being imported:

- *arr / Readarr / Lidarr / LazyLibrarian: the queue row's `id` from `/api/v3/queue` (or `/api/v1/queue` for Lidarr/Readarr). The `DownloadedXxxScan` payload uses it as `downloadClientID`.
- Bindery: the `bookId` from `/api/v1/queue`. The `manual-import` payload uses it as `bookId`.

Don't reset this field — it's the link between Harpoon2's item and the
manager's record.

### Container boot: app started before Postgres was reachable

Fixed by `wait_for_postgres()` / `wait_for_redis()` retry loops in
`entrypoint.sh`. If you hit a similar race on bare-metal Postgres, restart
the harpoon2 service after Postgres is fully up.

### rTorrent single-file torrents returning empty `f.multicall()`

Handled in code (`d.is_multi_file()` + `d.base_path()`). If you're writing a
custom downloader, follow the same pattern.

### `unrar` not found in container

`unrar` lives in Debian's `non-free` repo. The Dockerfile adds `non-free`
dynamically based on `VERSION_CODENAME`; if your base image changes you may
need to update the Dockerfile.

### More

- AGENTS.md → [Known issues / Lessons learned](AGENTS.md#known-issues--lessons-learned) for
  the canonical list.
- Container logs: `/var/log/harpoon2/celery-worker.log`,
  `celery-beat.log`, `django.log`, `supervisord.log` (rotated).
- Django shell inside the container: `ssh docker && docker exec harpoon2 python manage.py shell`.
