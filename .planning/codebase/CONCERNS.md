# Codebase Concerns

**Analysis Date:** 2026-08-28

## Tech Debt

### Duplicated `arr` post_process methods across manager subclasses
- **Issue:** `entities/managers.py` defines near-identical `post_process()` methods in `Sonarr` (lines 209-249), `Radarr` (266-298), `Lidarr` (325-357), `Readarr` (384-416), `Whisparr` (434-466) — and the base `Arr.post_process` (147-192) does the same work. Six copies of the same ~40-line block, varying only the command name (`DownloadedEpisodesScan` / `DownloadedMoviesScan` / `DownloadedAlbumsScan` / `DownloadedBooksScan`).
- **Files:** `entities/managers.py:147-466`
- **Impact:** Bug fixes have to be made in 6 places; easy to drift. Adding a new *arr manager (LazyLibrarian is listed in `MANAGER_TYPES` but has no class) means yet another copy.
- **Fix approach:** Single dispatch on command name via class attribute (`command_name = 'DownloadedEpisodesScan'`) and one `post_process` in the base `Arr` class.

### In-function `import` statements
- **Issue:** Every method in `entities/managers.py` re-runs `import requests`, `import logging`, `import os`, `import re`, `import shutil`, `import hashlib` at the top of its body. Same pattern in `entities/downloaders/qbittorrent.py` (`import qbittorrentapi`, `import bencoder`, `import time`, `import hashlib` inside `_init_client`), `entities/downloaders/rtorrent.py` (`import logging` in nearly every method), and `entities/tasks.py:212-213`, `itemqueue/tasks.py:62`.
- **Files:** `entities/managers.py` (40+ function-local imports), `entities/downloaders/rtorrent.py:142,158,211,253`, `entities/downloaders/qbittorrent.py:36-38,121,365`
- **Impact:** Module-imports re-hashed every call (small but real overhead); obscures real dependencies; makes static analysis (mypy, ruff) harder.
- **Fix approach:** Move imports to module top; gate optional/heavy ones (`qbittorrentapi`) behind lazy import only if that import has measurable cost in startup.

### Bare `pass` in exception handlers
- **Issue:** `entities/managers.py` has six `except: pass` blocks that silently swallow errors (lines 175, 233, 282, 341, 400, 450, 1523). Several swallow real failures (e.g. `SABnzbd` parent-folder resolution at line 415-416, Bindery `_detect_format` directory peek at 1519-1522).
- **Files:** `entities/managers.py:175,233,282,341,400,450,1523`, `itemqueue/tasks.py:373,416,420,814,1054`
- **Impact:** Failed operations look like success; the user has no signal that recovery was attempted. A bug in the recovery path becomes invisible.
- **Fix approach:** At minimum `logger.warning("...", exc_info=True)`; for non-trivial recovery code, branch explicitly on the known exception type and let unknown exceptions propagate.

### Dead / debug code in production paths
- **Issue:**
  - `harpoon2/views.py:83-85` — `print("DEBUG home: CALLED", file=sys.stderr)` runs on every dashboard load. Same pattern at line 550 `logger.error(f"=== clear_archive CALLED === ...")` used as a control-flow breadcrumb instead of a real log.
  - `harpoon2/views.py:969-979,983-989,1003,1011,1014-1015` — `api_item_transfers` emits `logger.error` for every transfer on every call, including happy-path serialization.
  - `entities/managers.py:378` — `print(r.json())` inside `Readarr.check_queue()`.
- **Files:** `harpoon2/views.py:83-85,550,557,564,566,969,973,975,979,983,987,1003,1011,1014`, `entities/managers.py:378`
- **Impact:** Pollutes logs (10-second `cache_downloader-status` beat makes the dashboard API hit at ~5s intervals; debug-level errors fill `/var/log/harpoon2/django.log` to its 10MB rotation threshold within hours of normal use). User-visible noise in production logs.
- **Fix approach:** Strip debug `print`/`logger.error` from production views; reserve `logger.error` for actual errors.

### Backward-compat alias hiding schema
- **Issue:** `entities/managers.py:1758` defines `Mylar = Mylar3` at module bottom, and `DOWNLOADER_TYPES` includes `Mylar` separately at `entities/urls.py:184`. `harpoon2/settings_template.py:290` lists both `('Mylar3', 'Mylar3')` and `('LazyLibrarian', 'LazyLibrarian')` but no `LazyLibrarian` class exists anywhere in the source tree.
- **Files:** `entities/managers.py:1758`, `harpoon2/settings_template.py:283-293`, `entities/views.py:187-189`
- **Impact:** `LazyLibrarian` manager can be selected in the form but throws `ImportError` at runtime when its class is referenced. `Mylar` choice is dead unless old DB rows reference it.
- **Fix approach:** Remove `LazyLibrarian` from `MANAGER_TYPES` until a class is implemented, or implement the stub. Decide on one of `Mylar`/`Mylar3` and migrate.

### Frustrating fallback in `Readarr.check_queue`
- **Issue:** `entities/managers.py:378` `print(r.json())` runs on every poll (every 20s) when Readarr is in use; this is the only `print` left in `managers.py` and the only one not behind a debug guard.
- **Files:** `entities/managers.py:378`
- **Impact:** Polls the 20-second `poll-managers` beat and the 5-minute `check-downloader-failures` beat against a remote *arr; stdout noise in the docker container.
- **Fix approach:** Delete the line.

### Stale `is_blackhole` parameter
- **Issue:** `entities/managers.py:1713` `Blackhole.should_skip_file` has `# TODO: Implement rename logic` for the `duplicate_handling == 'rename'` branch — it currently falls through to `return False` and effectively re-downloads.
- **Files:** `entities/managers.py:1695-1718`
- **Impact:** Selecting "rename" in the Blackhole config has no effect; users can be surprised by duplicates.
- **Fix approach:** Implement a real rename (suffixed `(1)`, `(2)` etc. on collision) or document the gap.

### In-function notifications
- **Issue:** The TODO comments `entities/tasks.py:291,343` ("Send notification about skipped file") have been open since 2024-03 (per the file's `git` timestamp); users who configure a Blackhole manager with no downloader silently lose files.
- **Files:** `entities/tasks.py:291,343`
- **Impact:** A user-configured Blackhole will silently drop .nzb/.torrent files if they forget to attach a downloader.
- **Fix approach:** Emit `Notification.create_for_admin` for skipped files with the file path.

## Known Bugs

### Duplicate `except Exception` block in `check_stalled_transfers`
- **Issue:** `itemqueue/tasks.py:1259-1265` — the first `try` block has two consecutive `except Exception` clauses (lines 1262-1263 and 1264-1265) with the same handler. This is a syntax-level duplicate (Python accepts it but it is clearly a copy-paste mistake). The second handler is unreachable.
- **Files:** `itemqueue/tasks.py:1259-1265`
- **Impact:** Cosmetic (no behavior change), but it is a known-bad pattern and `grep -n 'except' itemqueue/tasks.py` will continue to flag this. Also a sign the surrounding code was written under time pressure without review.
- **Fix approach:** Delete the duplicate block (lines 1264-1265).

### Bindery `Item.clientid` overflow risk
- **Issue:** `itemqueue/models.py:14` declares `clientid = models.IntegerField(default=0)` (32-bit signed max = 2,147,483,647). Bindery stores `bookId` here per `entities/managers.py:1164`. If a Bindery library ever exceeds 2.1B books, the column will overflow. More immediate: `int(item.clientid)` is performed in `entities/managers.py:1414` and assumes an int.
- **Files:** `itemqueue/models.py:14`, `entities/managers.py:1158,1164,1177,1414`
- **Impact:** Today fine, but a misnamed/misused field is a known footgun. `AGENTS.md` already calls out the field-name confusion ("Despite the name, it's not the *download client ID*").
- **Fix approach:** Rename to `manager_ref_id` in a migration; widen to `BigIntegerField` (or `CharField`) so the type matches the actual roles (Bindery `bookId`, arr `queue.id`).

### `int()` cast on optional `bookId`
- **Issue:** `entities/managers.py:1414` does `int(r.get('bookId') or 0)` — silently coerces missing/None to 0, then compares. A Bindery row with `bookId=null` matches every other row whose bookId was missing too. Spurious "in flight" matches.
- **Files:** `entities/managers.py:1401-1417`
- **Impact:** `Bindery._book_import_in_flight` may return `True` for items that have no real book, suppressing the manual-import we want to fire.
- **Fix approach:** `bookId` should be required for a Bindery row to count as in-flight; skip rows with null/0 bookId in the predicate.

### `os.rename` not cross-device safe
- **Issue:** `itemqueue/tasks.py:904` and `1341` use `os.rename(temp_folder, final_folder)`. The temp folder (Blackhole config) and the final library folder are very likely on different filesystems/mounts (different docker volumes), which makes `os.rename` raise `OSError: [Errno 18] Invalid cross-device link`.
- **Files:** `itemqueue/tasks.py:904,1341`
- **Impact:** Blackhole manager post-processing fails on the common case of temp on a fast scratch volume and final on a media library mount. Per-file `shutil.move` is used elsewhere; the rename here is an inconsistency.
- **Fix approach:** `shutil.move` is cross-device safe.

### SFTP connection timeouts not configurable
- **Issue:** `itemqueue/tasks.py:395` `sftp.get_channel().settimeout(60)` is hard-coded; the `transfer_files_async` task has `time_limit=3600`. For large single-file transfers (>1GB) the 60-second SFTP read timeout may fire mid-transfer on slow seedboxes.
- **Files:** `itemqueue/tasks.py:395,778`
- **Impact:** Large movie transfers on weak seedboxes (typical with overseas seedbox + VPN) hit the 60s SFTP channel timeout and the file is marked `failed`. Retries kick in (`max_retries=3`) but each retry re-transfers from the start (no resume).
- **Fix approach:** Read timeout from downloader options; default to 600s; consider SFTP resume (paramiko `sftp.prefetch` + offset-based `open().read()`).

### `SABnzbd` subfolder heuristic re-derives parent
- **Issue:** `itemqueue/tasks.py:401-420` tries to detect a "named volume" subfolder when SABnzbd gives us a file path, but only for a single-shape parent name (`' (X of Y)'`). Other shapes (e.g. with parens but no "of") fall through to `os.path.dirname(remote_dir)`, which may be a category dir, not a release dir.
- **Files:** `itemqueue/tasks.py:401-420`
- **Impact:** For NZBs in some categories, the post-transfer folder path is wrong and the arr manager's `DownloadedEpisodesScan` finds nothing.
- **Fix approach:** Use SABnzbd's `/api?mode=get_files&output=json&value=<nzo_id>` to get the actual file path the downloader used, rather than guessing from the path string.

### `WHITENOISE_AUTOREFRESH=True` in production
- **Issue:** `harpoon2/settings_template.py:304` sets `WHITENOISE_AUTOREFRESH = True`, which the WhiteNoise docs call "only useful during development" and warn against in production.
- **Files:** `harpoon2/settings_template.py:304`
- **Impact:** Production restarts will re-walk the static dir on every request, slowing first-byte. Almost certainly unintentional.
- **Fix approach:** `WHITENOISE_AUTOREFRESH = DEBUG` (the common pattern).

### `assign_items_to_downloaders` Bindery branch hits wrong API
- **Issue:** `entities/tasks.py:413-415` special-cases Bindery to use `/api/queue` (arr-compatible). AGENTS.md also flags this. The code does the right URL but then expects `records[].downloadId` to match `item.hash` (line 429) — correct for Bindery; but it also requires `downloadClient` field in the response (line 430). Bindery's `downloadClient` is a free-form string, not the Harpoon2 `Downloader.name` exactly.
- **Files:** `entities/tasks.py:413-470`
- **Impact:** Bindery items can fail to get a downloader back-filled even when one is configured; `Item.downloader = None` blocks `postprocess_item` from running, so the item sits in `Grabbed` indefinitely.
- **Fix approach:** Use Bindery's native `/api/v1/queue` (like `check_queue` does at `entities/managers.py:1044-1102`) and join on `downloadClient` against Harpoon2's `Downloader.name` case-insensitively.

### `queue_cronjob` is a vestigial name
- **Issue:** `entities/tasks.py:223-225` defines `queue_cronjob` that just calls `poll_managers` — its Celery beat schedule entry was removed (`harpoon2/celery.py` only schedules `poll-managers`), but the task itself is still callable. `entities/admin.py` (if it registers it) and any old cron job still pointing at it will keep polling.
- **Files:** `entities/tasks.py:223-225`
- **Impact:** Dead code; may be re-added to beat schedule by a future contributor who finds it via grep.
- **Fix approach:** Delete the task.

## Security Considerations

### Unauthenticated POST endpoints mutate state
- **Risk:** 11 of 14 state-changing endpoints in `harpoon2/views.py` lack `@login_required`. An unauthenticated request to `/cancel/download/<hash>/`, `/cancel/transfer/<name>/`, `/archive/all/failed/`, `/clear_archive/`, `/update/item/status/<hash>/`, `/update/item/downloader/<hash>/`, `/retry/failed/<hash>/`, `/retry/postprocessing/<hash>/`, `/cancel/postprocessing/<hash>/`, `/archive/<hash>/`, `/unarchive/<hash>/`, `/archive/all/completed/` can destroy a user's queue, change item statuses, force re-transfers, or wipe the archive.
- **Files:** `harpoon2/views.py:319,342,375,441,469,490,510,527,573,604,640` (vs. `545` which *does* have `@login_required`). Also `entities/views.py:172-199` `managertest` is missing `@login_required` and exposes the result of an authenticated API call to the manager.
- **Current mitigation:** None. CSRF middleware will reject POSTs without a token from a third-party origin, but a same-network attacker with `curl` can bypass by reading the login page first, and any XSS / open-redirector becomes a full state-mutation surface.
- **Recommendations:** Add `@login_required` to every view listed above. Or, more safely, switch to class-based `LoginRequiredMixin` CBVs and have the router enforce auth centrally.

### TLS verification disabled in downloader clients
- **Risk:** `entities/downloaders/sabnzbd.py:114` `verify=False` on the add-NZB POST; `entities/downloaders/sabnzbd.py:39` `self.client.verify = False` on the Session; `entities/downloaders/qbittorrent.py:62` `REQUESTS_ARGS={'verify': False}`. The qBittorrent instance is the typical attack target: the auth ban-backoff logic in `entities/downloaders/qbittorrent.py:23-78` shows we have already seen MITM-adjacent operational issues, so a real MITM would compound.
- **Files:** `entities/downloaders/sabnzbd.py:39,114`, `entities/downloaders/qbittorrent.py:62`
- **Current mitigation:** None. The comment says "Allow self-signed certs" — i.e., the operator is choosing this for convenience.
- **Recommendations:** Make `verify` configurable per-downloader; default to `True` and only disable when the operator opts in (and only for known CAs). For internal self-signed certs, mount the seedbox CA into the harpoon2 image and trust it.

### Plaintext secrets in `Downloader.options` / `Seedbox`
- **Risk:** `entities/models.py:145-146` stores `Seedbox.password` and `Seedbox.ssh_key` as `CharField`/`TextField` (no encryption); `entities/downloaders/sabnzbd.py:29` `self.apikey = opts.get('apikey', '')` and `qbittorrent.py:53` `self.password = opts.get('password', 'adminadmin')` store credentials in the `Downloader.options` JSONField. The **default password** for qBittorrent is `adminadmin` (line 53) — an unset qBittorrent config silently uses the well-known default. Logs include `f"QBittorrent client initialized: {self.username}@{self.host}:{self.port}"` (`entities/downloaders/qbittorrent.py:66`) and `f"SABnzbd cleanup] Connecting to {seedbox.host}:{seedbox.port}"` (`entities/downloaders/sabnzbd.py:404`), which is fine for usernames but shows the username in every line.
- **Files:** `entities/models.py:145-146,95`, `entities/downloaders/sabnzbd.py:29,39,114`, `entities/downloaders/qbittorrent.py:53,62,66`
- **Current mitigation:** DB file is at `/data/harpoon2.db` (SQLite) or `postgres` (Docker). No disk encryption mentioned in `DEPLOYMENT.md`.
- **Recommendations:** Move secrets to a vault (HashiCorp Vault, age-encrypted file mounted at runtime). Remove the `adminadmin` default. Hash `apikey` for comparison rather than storing in plaintext.

### Credentials in rTorrent XMLRPC URL
- **Risk:** `entities/downloaders/rtorrent.py:170-179` builds `f"{protocol}://{auth}{host}:{port}/{url_path}"` with `user:pass@` and passes it to `xmlrpc.client.ServerProxy`. The URL string contains plaintext credentials, which the lib/rtorrent `RTorrent` class logs in `RTorrent.__init__` (`lib/rtorrent/__init__.py`). Any process that captures process env, strace, or `/proc/<pid>/cmdline` sees the seedbox password.
- **Files:** `entities/downloaders/rtorrent.py:170-179,180`, `lib/rtorrent/__init__.py`
- **Current mitigation:** None.
- **Recommendations:** Use HTTP basic-auth on the `ServerProxy` instead of embedding in the URL (`ServerProxy(url, transport=BasicAuthTransport(user, pass))`); the URL stays opaque in logs.

### `SECRET_KEY` default in settings
- **Risk:** `harpoon2/settings_template.py:24` `SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')`. If the operator forgets to set the env var (e.g., in a fresh container that mounts a stale `.env`), every signed value Django produces is forgeable: session cookies, password-reset tokens, CSRF tokens.
- **Files:** `harpoon2/settings_template.py:24`
- **Current mitigation:** `.env.example:5` reminds to change it, but `harpoon2/celery.py` and `harpoon2/wsgi.py` will silently boot with the default.
- **Recommendations:** Refuse to start when `SECRET_KEY == 'change-me-in-production'` (raise in settings).

### `DEBUG` not asserted false in production
- **Risk:** `harpoon2/settings_template.py:27` `DEBUG = os.environ.get('DEBUG', 'False') == 'True'`. A typo (`'true'` instead of `'True'`, `'1'`, etc.) silently disables debug. Django's debug view exposes environment, request bodies, and SQL.
- **Files:** `harpoon2/settings_template.py:27`
- **Current mitigation:** `entrypoint.sh` is described in AGENTS.md to wait on Postgres/Redis; no `DEBUG` check.
- **Recommendations:** Parse strictly (allow only `'True'`/`'False'`), and assert `not DEBUG` in `entrypoint.sh` for non-dev image tags.

### Unauthenticated API endpoints leak data
- **Risk:** `/api/queue/`, `/api/history/`, `/api/dashboard/`, `/api/item/<hash>/history/`, `/api/item/<hash>/transfers/` (all in `harpoon2/views.py:854,910,672,932,960`) and `/api/version/` (1019) lack `@login_required`. Anyone on the network (or any XSS via a CORS-bypassed iframe) can enumerate the queue, history, and per-file transfer progress — which leaks user download history, including item names, hashes, file sizes, and timestamps. `/api/version/` discloses the running version, helping attackers fingerprint known CVEs.
- **Files:** `harpoon2/views.py:672,854,910,932,960,1019`
- **Current mitigation:** None. CSRF doesn't apply to GET.
- **Recommendations:** Add `@login_required` to all five endpoints; gate `/api/version/` behind `request.user.is_staff`.

### No rate limit on login_view
- **Risk:** `harpoon2/views.py:17-77` accepts any number of login attempts. With `LOGIN_URL = '/login/'` exposing the superuser creation form when no superuser exists, the username/password brute-force surface is the public internet (if exposed).
- **Files:** `harpoon2/views.py:17-77`, `harpoon2/settings_template.py:108`
- **Current mitigation:** `AUTH_PASSWORD_VALIDATORS` includes `CommonPasswordValidator`, but no lockout/throttle.
- **Recommendations:** Install `django-axes` or `django-ratelimit`; require email-confirmation for the initial superuser creation flow.

### Logger error level misused
- **Risk:** `harpoon2/views.py:550,557,564,566,969,973,975,979,983,987,1003,1011,1014` use `logger.error(...)` for non-error events (e.g., "Found N items", "Processing transfer N"). If monitoring/alerting is wired to `ERROR` level (e.g., Sentry), these will fire false pages.
- **Files:** `harpoon2/views.py:550,557,564,566,969,973,975,979,983,987,1003,1011,1014`
- **Current mitigation:** None.
- **Recommendations:** Demote to `logger.debug`/`logger.info`; reserve `logger.error` for actual exceptions.

## Performance Bottlenecks

### N+1 query in `home` and `api_dashboard`
- **Problem:** `harpoon2/views.py:129-133` and `720-730` pre-compute item total sizes by filtering the queryset inside a loop:
  ```python
  for transfer in active_transfers:
      ...
      all_transfers_for_item = active_transfers.filter(item__hash=item_hash)
      item_total_sizes[item_hash] = sum(t.file_size for t in all_transfers_for_item)
  ```
  Each loop iteration re-queries the same `FileTransfer` table, scaling O(N²) with active transfers. For an operator with hundreds of post-processing items, the dashboard can spend seconds in queries.
- **Files:** `harpoon2/views.py:128-133,724-731`
- **Cause:** Calling `.filter()` on a sliced QuerySet would error (see comment at line 887), so the pre-pass hides the issue by querying a fresh filtered set.
- **Improvement path:** Aggregate via SQL: `FileTransfer.objects.filter(...).values('item__hash').annotate(total=Sum('file_size'))` returns a dict in one query.

### N+1 on `item_total_completed` aggregation
- **Problem:** `harpoon2/views.py:138-172` and `733-769` loop the same `active_transfers` queryset to aggregate `total_completed`, `earliest_start`, `latest_update`. These are aggregations on the same set and can be expressed in a single SQL pass.
- **Files:** `harpoon2/views.py:138-172,733-769`
- **Improvement path:** Use `Sum`, `Min`, `Max` aggregations grouped by `item__hash` (Django ORM `aggregate()` on a `values('item__hash')` queryset).

### Dashboard full re-aggregation on every poll
- **Problem:** `api_dashboard` is the AJAX poll endpoint. With `cache-downloader-status` beating every 10s and the dashboard refreshing every 2-5s in typical use, the same N+1 query runs many times per minute.
- **Files:** `harpoon2/views.py:672-852`
- **Improvement path:** Cache the aggregation in Redis with a 1-2s TTL; or use `CachedDownloaderStatus`-style pattern (write a `CachedDashboardSummary` row from a beat task instead of computing on read).

### SABnzbd per-request history fetch
- **Problem:** `entities/downloaders/sabnzbd.py:142` `_api_call('queue', {'limit': 100})` per `find()`; `get_status` calls `find` and may call `_api_call('history')` next (`sabnzbd.py:174`). `check_stalled_transfers` and `get_completed` each do their own history fetch.
- **Files:** `entities/downloaders/sabnzbd.py:142,174,295`, `itemqueue/tasks.py:1466`
- **Improvement path:** Cache the history response in `CachedDownloaderStatus` (same model used for `cache-downloader-status`).

### Single-threaded Celery worker on 5+ beat tasks at 20s
- **Problem:** `harpoon2/celery.py:31-53` schedules `poll-managers`, `poll-blackhole-managers`, `assign-items`, `check-downloaders`, `check-stalled-transfers` all at 20s intervals. Combined with `task_acks_late=True` and `worker_prefetch_multiplier=1`, the worker is mostly idle but each task can block when an *arr API is slow. With 5 managers + 4 downloaders, a single beat worker performs 5+4=9 sequential API calls every 20s, plus the Bindery queue re-fetches already in `_queue_record_for_item`/`_book_import_in_flight`.
- **Files:** `harpoon2/celery.py:29-69`
- **Improvement path:** Run Celery with concurrency=2 and route beat tasks to their own queue; or move the per-manager poll to a thread pool.

### Bindery `_queue_records()` called 3× per item
- **Problem:** In `Bindery.post_process` (`entities/managers.py:1286-1356`), for each item the code calls `_queue_record_for_item` (1 fetch), `_book_import_in_flight` (another fetch), and `_manual_import_new` (which doesn't fetch). That's two full Bindery queue fetches per item per recovery. With a backlog of failed items, this becomes the dominant poll cost.
- **Files:** `entities/managers.py:1286-1356,1390,1401`
- **Improvement path:** Cache `_queue_records()` for the duration of the `post_process` call (module-level TTL'd cache or pass it in).

## Fragile Areas

### `check_stalled_transfers` per-item per-tick
- **Files:** `itemqueue/tasks.py:1196-1357`
- **Why fragile:** The function loops every `FileTransfer` and every `Item.objects.filter(status='PostProcessing')` on every 20s tick. Combined with the duplicate `except` block (line 1262-1265), the per-item section has at least 5 different paths to "mark as failed/Grabbed/Completed" with subtly different rules. Changing the rule (e.g., to ignore the most recent 5 minutes after a retry_postprocessing call) is hard.
- **Safe modification:** Add a unit test that constructs a known PostProcessing item + a known failed transfer, asserts the function does NOT mark the item as Failed twice in a row. Modify only the new behavior with that test as a guard.
- **Test coverage:** None.

### Mylar3 log-message scraping
- **Files:** `entities/managers.py:612-727` (Mylar3.poll)
- **Why fragile:** The poll parses Mylar3's log lines by string match against `attempting to download` and bracket-prefixes (`[AIRDCPP]`, `[RTORRENT]`, etc.). If Mylar3 changes its log wording — e.g. to "Starting download" or moves to a different locale — the entire poll silently stops finding new items. The hash is `md5(comic_name)` (`entities/managers.py:689`) — two comics with the same name across years/series collide on hash, and a re-grab is treated as the same item.
- **Safe modification:** Wrap any change in a "sample real Mylar3 log" test fixture. Consider using Mylar3's own API for queue/status (Mylar3 has `getWanted`, `getHistory`) instead of scraping.
- **Test coverage:** None.

### Bindery manual-import recovery logic
- **Files:** `entities/managers.py:1185-1509`
- **Why fragile:** The `post_process` flow now has three different recovery branches (`_book_import_in_flight`, `_queue_record_for_item`, `_manual_import_new`) and side effects (deletes a Bindery queue row, calls manual-import/match, calls manual-import). Subtle order-of-operations and idempotency assumptions are encoded in comments but not in tests. The recovery was added recently and the comments are the spec.
- **Safe modification:** Add a fake Bindery test double (responses for each of the four code paths), exercise `Bindery.post_process` end-to-end. Verify: (1) success returns `(True, msg)`, (2) duplicate calls are idempotent, (3) the stale-row delete is only attempted for `importfailed`/`importblocked`.
- **Test coverage:** None.

### `transfer_files_async` monolithic task
- **Files:** `itemqueue/tasks.py:292-1054`
- **Why fragile:** The function is 760 lines and contains 6 logical phases (download-info, SFTP-connect, file-walk, transfer, archive-extract, post-process, status-update) plus error handling for each. The known-bad pattern at line 1262-1265 in a sister task suggests the same author wrote the whole pipeline in one sitting; future changes will trip the same edit-revert loop. AGENTS.md mentions "Multiple re-runs of `transfer_files_async` create duplicate `FileTransfer` records" — fixed but only by the `existing_transfers.exists()` short-circuit at line 654.
- **Safe modification:** Refactor into composable steps with a state-machine driver. Add regression tests for the duplicate-detection logic.
- **Test coverage:** None.

### `Mylar3` downloader inference by log prefix
- **Files:** `entities/managers.py:677-686`
- **Why fragile:** Per AGENTS.md, Mylar3's API has no `/api/v3/queue`, so Harpoon2 falls back to log scraping and then guesses the downloader by the bracketed prefix in the log message. The bracket prefixes are hard-coded (`[SABNZBD]`, `[AIRDCPP]`, etc.) — the `entities/managers.py:668` list also has lowercase variants. A Mylar3 release that renames the prefix (e.g. to `[SAB]`) silently breaks downloader assignment for every new item.
- **Safe modification:** Provide a UI mapping (admin form) to map log prefix → Harpoon2 downloader. Add a regression test that pins the prefix list.
- **Test coverage:** None.

### Docker race condition
- **Files:** `docker-compose.yml`, `entrypoint.sh`, `AGENTS.md` ("Race condition on container reboot")
- **Why fragile:** Per AGENTS.md, `depends_on + healthcheck` lets the app start before Postgres/Redis are accepting. `entrypoint.sh` has `wait_for_postgres()`/`wait_for_redis()` retry loops. This works but is fragile: a slow first-time Postgres start (e.g. on a low-resource VPS) can exceed the retry budget and `migrate` fails; the container then exits and Docker's restart policy loops the same race.
- **Safe modification:** Add `restart: on-failure` and bump the retry budget to 60s+; or use an init container pattern.

## Scaling Limits

### Dashboard query scaling
- **Current capacity:** Linear scan of `FileTransfer` and `Item` per dashboard render. With ~1000 post-processed items in DB and ~50 active transfers, the N+1 in `home`/`api_dashboard` takes ~500ms on a fast machine; a single Postgres query could do it in 20ms.
- **Limit:** Tens of thousands of `FileTransfer` rows before the dashboard becomes painful; tens of millions before Django ORM timeouts.
- **Scaling path:** Aggregation SQL; periodically archive completed `FileTransfer` rows; use a materialized `ItemSummary` table refreshed by beat.

### `ItemHistory` table growth
- **Current capacity:** A typical 5-minute-history item gets 10+ history rows (Started, Downloaded, SFTP progress, Extracted, PostProcessing, Completed). 1000 items/month = 10k history rows; 100k items = 1M rows. `Item.history` ordering and the recent-10min `ItemHistory.objects.filter(... details__icontains='Post-processing').exists()` lookup in `itemqueue/tasks.py:1286-1290` becomes a full table scan when no index covers `details`.
- **Limit:** Millions of rows.
- **Scaling path:** Add index on `ItemHistory(details)` (or the prefix `details LIKE 'Post-processing%'`); periodically prune old histories.

### Item `name` field 500-char limit
- **Files:** `itemqueue/models.py:7`
- **Current capacity:** 500 chars. Sufficient for normal release names; some international titles or compound names hit the wall.
- **Limit:** Releases with extended names truncate and break the sanitization in `itemqueue/tasks.py:467,937,1395` (`re.sub(r'[<>:"/\\|?*]', '', item.name)`).
- **Scaling path:** Widen to 1000 chars; or store on a separate `ReleaseName` table.

### Manager polling at 20s on shared beat
- **Files:** `harpoon2/celery.py:31-69`
- **Current capacity:** A handful of managers per 20s tick. Beyond ~20 managers, the poll loop will overrun 20s and beat will skip ticks.
- **Scaling path:** Per-manager independent beat schedule; or use celery groups.

## Dependencies at Risk

### `rtorrent-python` (Pipfile pinned 3.8, lib/rtorrent is local fork)
- **Risk:** `Pipfile` pins `python_version = "3.8"`, but `requirements.txt` and `Dockerfile` use a newer Python. The local `lib/rtorrent/` is a vendored fork of `rtorrent-python`; no upstream maintenance signal. If a security fix is needed in `paramiko` (which `lib/rtorrent` uses) we have to maintain the fork.
- **Files:** `Pipfile`, `lib/rtorrent/`
- **Migration plan:** Drop the vendored `lib/rtorrent/` and use `rtorrent-rpc` (maintained fork) or a direct `xmlrpc.client.ServerProxy` wrapper (which `entities/downloaders/rtorrent.py:6-128` already shows we know how to do).

### `celery==5.0.0rc3` in Pipfile vs `celery>=5.4` in requirements.txt
- **Risk:** `Pipfile:8` pins `celery = "==5.0.0rc3"` while `requirements.txt:2` allows `celery>=5.4,<6.0`. If a developer uses Pipenv, they get the pre-release 5.0.0rc3; if they use pip, they get 5.4+. The two will diverge in behavior — the 5.0 RC had different task-routing defaults. This is invisible until something breaks.
- **Files:** `Pipfile:8`, `requirements.txt:2`
- **Migration plan:** Pick one source of truth; pin to a stable Celery 5.4.x; delete `Pipfile` or `requirements.txt` whichever is not canonical.

### `psycopg2-binary` in production
- **Files:** `requirements.txt:4`
- **Risk:** `psycopg2-binary` is convenient but not recommended for production (the PyPI docs say so). For long-running Celery workers + Django + Gunicorn, leaks are possible.
- **Migration plan:** Switch to `psycopg2` (compiled) in production Dockerfile.

### `paramiko` for SFTP
- **Files:** `requirements.txt:10`
- **Risk:** `paramiko` is the source of most seedbox auth complexity. CVE-2023-48795 (Terrapin) requires `paramiko>=3.4`. Pin to the latest 3.x release.
- **Migration plan:** `pip-audit` in CI; pin to `paramiko>=3.4` in `requirements.txt`.

## Missing Critical Features

### No retry/circuit breaker for manager API
- **Problem:** `entities/tasks.py:21-219` `poll_manager` does a single `requests.get(...)` with no retry on transient failure. If Sonarr is restarting (1-2 minutes during an update), the entire 20s tick logs a warning and gives up. The Item is still in `Grabbed` and doesn't progress until the next tick — fine, but if the next tick *also* fails, no exponential backoff.
- **Blocks:** Resilience to manager restarts.

### No backfill for missing `Item.downloader`
- **Problem:** Items without a downloader cannot run `postprocess_item` (line 1069 of `itemqueue/tasks.py`). The `assign_items_to_downloaders` task tries to fix this every 20s but uses fuzzy name matching (`name__icontains=download_client`) which can match the wrong downloader when names overlap (e.g., "SABnzbd" matching a downloader named "BigSABnzbd" in the operator's config).
- **Blocks:** Stale `Grabbed` items when the manager poll doesn't return a `downloadClient`.

### No queue depth metric / dashboard slowdown warning
- **Problem:** No way for the operator to know that the queue is backing up faster than transfers can drain.
- **Blocks:** Operational awareness.

### No support for multiple simultaneous seedboxes per Downloader
- **Files:** `entities/models.py:96` — `Downloader.seedbox` is a single FK.
- **Blocks:** Operators with a pool of seedboxes (common for high-volume operators) cannot have one SABnzbd spread across them; they have to create N `Downloader` rows and manage routing manually.

## Test Coverage Gaps

### `itemqueue/tests.py` and `entities/tests.py` are empty
- **Files:** `itemqueue/tests.py`, `entities/tests.py`, `users/tests.py` — all 60 bytes of `# Create your tests here.`.
- **What's not tested:** Every Celery task in `itemqueue/tasks.py` and `entities/tasks.py`, every manager class in `entities/managers.py`, every downloader class in `entities/downloaders/`, every view in `harpoon2/views.py` and `entities/views.py`. The transfer pipeline (`transfer_files_async`, 760 lines) has no test coverage.
- **Risk:** A change to `transfer_files_async` (e.g., the dedup logic at line 686-700) can silently regress; the only signal is the dashboard showing wrong file sizes (which is what AGENTS.md says happened before).
- **Priority:** High. The transfer pipeline is the heart of the app; a single regression in dedup or post-process state transition is a 4-hour debugging session for the operator.

### No fixtures for `Manager` / `Downloader` / `Item` test data
- **What's not tested:** `AGENTS.md` lists an `assign_missing_downloaders` management command (`entities/management/commands/assign_missing_downloaders.py`) that was added to recover from the very bug AGENTS.md describes; that command has no tests.
- **Risk:** Future regressions in downloader matching will be caught late.
- **Priority:** Medium.

### No integration tests for the manager/downloader polling → post-process cycle
- **What's not tested:** End-to-end "manager reports grabbed → downloader completes → transfer runs → manager post-process succeeds" loop.
- **Risk:** Any regression in the contract between `check_downloaders`, `postprocess_item`, `transfer_files_async`, and the manager `post_process` will only show up in the operator's queue.
- **Priority:** High.
