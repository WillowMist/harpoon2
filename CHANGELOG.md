# Changelog

All notable changes to Harpoon2 are documented in this file. Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.3] - 2026-03-23

### Fixed

- **Radarr/Sonarr post-processing.** Post-processing requests now use the correct `downloadClientID` format (`clientid` as a string instead of hash). Fixed a `NullReferenceException` in Radarr post-processing. Files are now reliably sent to Radarr/Sonarr for import.
- **SABnzbd cleanup.** Fixed a critical bug in the SABnzbd cleanup method (`'bool' object is not callable`). Cleanup now properly deletes downloaded files from the seedbox after post-processing. Renamed an internal attribute to avoid method shadowing.
- **Downloader assignment.** Fixed automatic downloader assignment from manager history responses. Added a management command to retroactively assign downloaders to items missing one. Downloaders are now matched by name or type (case-insensitive).
- **Database performance.** Removed 720,919+ duplicate "Import event received" spam records from `ItemHistory`. Fixed polling to prevent repeated import-event logging for completed items. Significantly improved history page load times.
- All managers (Radarr, Sonarr, Lidarr, Readarr, Whisparr) now have consistent post-processing handling.
- Better error handling and logging for post-processing failures.

## [2.1.4] - 2026-08-28

### Added

- **Bindery manager type** ([`vavallee/bindery`](https://github.com/vavallee/bindery) for ebooks/audiobooks). Queue polling via Bindery-native `/api/v1/queue`; downloader back-fill via arr-compatible `/api/queue`; manual-import post-processing with per-format staging folders (`bindery_ebook_folder`, `bindery_audiobook_folder`); path remap (`from:to`, longest-prefix-first) for telling Bindery about paths it doesn't natively see; transient-error substring retry; stale `importFailed` / `importBlocked` row cleanup after a successful import of the same book; book-level dedup so an `imported` row for a book marks the item `Completed` even if an earlier `importFailed` row lingers.
- **Mylar3 log-based polling.** Mylar3's queue is read from its download log (the only public surface Mylar3 exposes for "Attempting to download" events), not from a queue API.
- **qBittorrent ban-backoff.** After a 401/403 from qBittorrent, the client backs off for 30 minutes before re-authenticating. Prevents an auth-failure loop from extending the ban indefinitely.
- **Django admin registrations** and dedupe of `FileTransfer` records.
- **Test infrastructure.** `pytest` + `pytest-django` + `responses` + `freezegun` via new `requirements-dev.txt`; `pytest.ini`, root `conftest.py`, `tests/__init__.py`, and one passing smoke test in `tests/test_smoke.py`.
- **`tenacity>=9.1,<10`** runtime dependency (preparation for the Phase 5 retry/backoff work; no callers yet).
- **`unrar`** in the Docker image; the Dockerfile now enables Debian's `non-free` repo so the `unrar` package resolves.
- **Supervisor** runs `celery-beat`, `celery-worker`, and `django` as supervised processes inside the container, replacing the previous multi-process entrypoint.
- **`USER_GUIDE.md`** operator documentation.

### Changed

- **`paramiko>=3.4,<6.0`** — closes CVE-2023-48795 (Terrapin). The 5.0.x line is API-compatible with Harpoon2's `SFTPClient` usage.
- **`WHITENOISE_AUTOREFRESH = DEBUG`** — binds WhiteNoise's static-dir re-walk to Django's `DEBUG` flag, so production requests stop paying the re-walk cost while dev keeps live-reload.
- **Entrypoint now waits** for Postgres and Redis (with retry loops) before running migrations. Closes a race where the app container could start before its dependencies were accepting connections.
- **`Manager.apikey`** field widened to 100 chars to fit Bindery's longer API keys.
- **`Item.name`** widened to 500 chars to fit long book titles.
- **AirDC++** event polling limits doubled (20→40, 40→80); added a `polling_history` config option (default 100).
- **`entrypoint.sh`** rewritten to use Supervisor; the old hand-rolled wait/launch logic is gone.
- **`AGENTS.md`** expanded with sections on the Bindery adapter, transfer pipeline, and the three-root Bindery layout (ebook destination / audiobook destination / staging-only).

### Fixed

- **qBittorrent single-file detection.** The previous heuristic (`'.' in t.name`) misclassified any torrent whose directory name contains a period (`Mr. Monster`, `Vol.1`, `10.4`, `A.X.E....pdf`) as a single file. The transfer pipeline then called `sftp.get()` on a directory path, the seedbox rejected the read with `SSH_FX_FAILURE` after 3 retries, and Bindery's post-process then failed with "No staged file/folder found". Detection now uses `torrents_files()` — a torrent is single-file only when the API reports exactly one file at the top of `save_path`.
- **RTorrent single-file detection** rewritten to use `d.is_multi_file()` instead of `f.multicall()` (which returns empty for single-file torrents in some rTorrent versions).
- **Transfer pipeline race conditions** — verify all `FileTransfer` records complete before moving the staged folder to its final destination; prevent accidental deletion of the final folder when extraction is in progress; always create a per-item subfolder for Blackhole items.
- **`walk_remote_sftp` recursion bug** — function was missing its `def` statement, declared `nonlocal` outside its scope, and was nested inside an AirDC++-only `if` block. Generalized to all downloaders and corrected.
- **Mylar3 `forceProcess` payload** — split folder/filename, strip extension from `nzb_name`, send parameters as URL query (not JSON body), use year-then-issue-count matching to find the right comic, prefer series with more issues over TPBs/one-shots.
- **Mylar3 post-processing** no longer re-runs every 20 seconds on already-completed items, and falls back to `item.name` when `download_path` is a directory.
- **`assign_items_to_downloaders`** now includes `PostProcessing` items in its filter (was excluding them, leaving items without a downloader assignment).
- **Manager form save handler** rebound to `mousedown` with capture, fixing intermittent save failures from a Bootstrap modal click handler eating the submit.
- **`/api/queue` URL** in the Bindery `assign_items_to_downloaders` call corrected.
- **`clear_archive`** and **Cancel Transfer** flows reworked (login required, GET support, method logging, archive-only filter on `archived` view, deletes on cancel).
- Numerous downloader path-construction, indentation, and import fixes accumulated over the 5-month cycle.

### Removed

- **`djangorestframework`**, **`django-celery-results`**, **`django-extensions`** from `requirements.txt` — declared but never imported or registered in `INSTALLED_APPS`.
- **`Pipfile`** and **`Pipfile.lock`** — `requirements.txt` is now the sole install spec. The deleted `Pipfile` had a pre-release pin (`celery == "5.0.0rc3"`) that drifted from `requirements.txt`'s `celery >= 5.4`.
- **`pysftp`** (was in `Pipfile` only; `pysftp`'s final release is 0.2.9 from 2022 and the project is unmaintained).

### Security

- **`paramiko>=3.4,<6.0`** — closes CVE-2023-48795 (Terrapin attack against the SSH binary packet protocol).

### Notes for the next release

Two known concerns documented in `.planning/STATE.md` for the upcoming Phase 2 / Phase 3 / Phase 5 work:

- `Manager.poll_interval` is a dead field — the form exposes it and the model stores it, but `harpoon2/celery.py` hard-codes the `poll-managers` beat schedule to 20s. Wire into a per-manager schedule or drop the field.
- `transfer_files_async` ↔ Bindery `check_queue` race: `check_queue` can flip an item to `Failed` mid-transfer if Bindery's API still reports the row as `failed` (no import yet). Currently self-heals within ~20s of the manual-import landing; operator confirmed this is acceptable. Phase 5 should not introduce "skip on `Failed`" gating that would break the self-heal.
