# Coding Conventions

**Analysis Date:** 2026-08-28

## Naming Patterns

**Files:**
- Modules are lowercase, snake_case: `entities/models.py`, `itemqueue/tasks.py`, `users/signals.py`, `dplibs/session.py`
- Django app layout is the default `startapp` style: `models.py`, `views.py`, `forms.py`, `urls.py`, `admin.py`, `apps.py`, `tasks.py`, `tests.py` per app
- Test stubs are the default `tests.py` in each app (no `test_*.py`, no `tests/` subdirectory, no `conftest.py`)
- The `lib/` directory contains vendored, non-app code (`lib/rtorrent/`, `lib/bencode.py`, `lib/torrentparser.py`)
- A single root-level helper module: `dplibs/session.py`, `dplibs/search.py` (not an installed app — uses `@login_required` directly)

**Functions:**
- snake_case throughout, including methods: `get_or_create`, `check_queue`, `checkoptions`, `send_to_downloader`, `extract_rar_archive`
- Predicates use the existing helpers: `is_authenticated`, `is_superuser`, `is_single_file`, `is_complete`, `is_active`
- Dunder `__str__` is implemented per model that has a meaningful display value (`entities/models.py:13, 98, 149`; `itemqueue/models.py` is missing it for `Item`, `ItemHistory`, `FileTransfer` — see CONCERNS for follow-up)

**Variables:**
- snake_case; `clientid` is the historical name kept for backward compatibility (see AGENTS.md "Per-manager import-target ID")
- Short-lived locals in long task functions are deliberately lowercase (`item_hash`, `download_id`, `manager_client`); the codebase does not abbreviate further
- Constants are UPPER_SNAKE at the top of `lib/rtorrent/...` files and in the manager classes (e.g. `Bindery.STATUS_MAP`, `Bindery.DEFAULT_TRANSIENT_ERROR_SUBSTRING`, `QBittorrentDownloader.BAN_BACKOFF_SECONDS`)

**Types:**
- Classes are PascalCase: `BaseDownloader`, `RTorrentDownloader`, `SABnzbdDownloader`, `AirDCppDownloader`, `QBittorrentDownloader`, `Arr`, `Sonarr`, `Radarr`, `Lidarr`, `Readarr`, `Whisparr`, `Mylar3`, `Bindery`, `Blackhole`, `CustomUser`, `Notification`, `NotificationSettings`
- The `BaseDownloader` ABC is named with the `Base` prefix; concrete subclasses use a `<Name>Downloader` suffix (`RTorrentDownloader`, `SABnzbdDownloader`, `AirDCppDownloader`, `QBittorrentDownloader`). Each concrete class also re-exports the legacy short name (`RTorrent`, `SABNzbd`, `AirDCpp`, `QBittorrent`) as a factory function in `entities/downloaders/__init__.py`
- Manager classes don't share an ABC; `Arr` is the *arr base class, but `Mylar3`, `Bindery`, and `Blackhole` are independent (defined in `entities/managers.py`)

## Code Style

**Formatting:**
- PEP 8 with 4-space indentation. No black/ruff/autopep8 config files exist in the repo (no `.black`, no `pyproject.toml`, no `.flake8`, no `.pylintrc`)
- Single quotes for strings by default; f-strings used heavily for log messages and JSON-shape construction (e.g. `entities/managers.py:482`, `entities/managers.py:569`, `entities/tasks.py:88-92`, `entities/managers.py:103-105`)
- Double quotes appear for SQL/log messages where the value already contains a single quote (e.g. `entities/managers.py:40`, `logger.error(f"Error assigning downloader for item {record['name']}: {e}")`)
- Comments use `#`; multi-line block comments use `# ` repeated; docstrings use `"""..."""` (see `entities/managers.py` for the dominant pattern)
- Trailing commas are used in multi-line literals; alignment via 4-space indents

**Linting:**
- No linter is configured in the repo. There's no `.flake8`, no `pyproject.toml`, no `pre-commit` config, and no CI lint step in `.github/workflows/docker.yml` (the workflow only builds/pushes the Docker image)

**Type hints:**
- Used selectively. Type hints appear in `entities/downloaders/base.py` (`abstractmethod` signatures use `-> str`, `-> tuple`, `-> dict`, `-> list`), in `entities/downloaders/airdcpp.py` (`Optional[Dict]`, `List[Dict]`), and in some Celery task signatures
- The vast majority of the codebase — including the long `entities/managers.py` (1,758 lines) and `itemqueue/tasks.py` (1,526 lines) — is untyped
- When present, hints use Python 3.9+ syntax (`list[dict]`, `tuple[bool, str]`) is not used; the codebase prefers the `typing` module equivalents

## Import Organization

**Order (observed across the project):**
1. Standard library (`os`, `re`, `json`, `subprocess`, `glob`, `hashlib`, `logging`, `abc`, `typing`)
2. Third-party packages (`django.db`, `django.shortcuts`, `django.http`, `celery`, `requests`, `paramiko`, `paramiko.SSHClient`, `qbittorrentapi`, `pytz`)
3. First-party `harpoon2.*` (e.g. `from harpoon2.celery import app`, `from harpoon2.settings import MANAGER_TYPES, DOWNLOADER_TYPES`)
4. Local app imports (`from . import managers, downloaders`, `from .models import ...`, `from .base import BaseDownloader`, `from itemqueue.models import Item, ItemHistory, FileTransfer`)

**Path Aliases:**
- No path aliases. Imports always use the full dotted path. Examples: `from harpoon2.celery import app` (`entities/tasks.py:2`), `from harpoon2.settings import MANAGER_TYPES, DOWNLOADER_TYPES` (`entities/models.py:3`), `from itemqueue.models import Item, ItemHistory, FileTransfer` (`entities/tasks.py:3`)
- `harpoon2/settings.py` is a **symlink** to `harpoon2/settings_template.py` (Dockerized deployments copy the template into `/data/settings.py`; the symlink exists for local dev). Confirmed at the filesystem level: `harpoon2/settings.py -> harpoon2/settings_template.py`

**Imports inside functions:**
- Common and intentional. Roughly 30+ `import` statements occur inside function bodies to break circular dependencies or avoid import-time side effects:
  - `from entities.models import Downloader, Seedbox` inside downloader cleanup paths (`entities/downloaders/sabnzbd.py:391`)
  - `from itemqueue.models import FileTransfer` inside `entities/managers.py` methods
  - `from users.models import Notification` inside `entities/managers.py:1747` and `harpoon2/views.py:397, 412`
  - `import logging` + `logger = logging.getLogger(__name__)` declared lazily inside functions in `entities/managers.py` (multiple locations) and `entities/tasks.py:233, 250`
  - `import hashlib`, `import requests` deferred inside `Mylar3.poll()` (`entities/managers.py:619-621`)

## Error Handling

**Strategy:**
- Broad `try / except Exception` is the dominant pattern: 154 occurrences across the project (excluding `lib/rtorrent/`). Used to keep long-running Celery tasks and API endpoints resilient when an external service (Sonarr, SABnzbd, qBittorrent, Bindery, AirDC++, Mylar3, GitHub) misbehaves
- Functions that touch external services return a `(success: bool, message: str)` tuple rather than raising. This is the **house style for downloader/manager clients**:
  - `BaseDownloader.test() -> tuple`
  - `BaseDownloader.verify_completion() -> tuple`
  - `BaseDownloader.cleanup() -> tuple`
  - `Arr.test()`, `Arr.check_queue()`, `Mylar3.test()`, `Mylar3.get_history()`, `Bindery.test()`, `Bindery.check_queue()`, `Bindery._manual_import_new()`, `Bindery._queue_records()`, `Bindery._delete_stale_original_row()` all return `(False, str(e))` on failure
- JSON views return `JsonResponse({'success': False, 'error': '...'}, status=...)` rather than letting exceptions propagate (see `harpoon2/views.py:67-69`, `entities/views.py:215, 224, 287`)
- Bare `except:` is avoided; the only bare excepts in the project are `except:` for `response.json()` failures (e.g. `entities/managers.py:174, 232, 281, 340, 399, 449`) — these swallow the ValueError raised by `requests` when the body isn't JSON, which is documented in the surrounding code
- Bare `except Exception` is used to capture *any* failure for logging + safe-fallback behaviour, particularly around `mylar3.poll()` and `cache_downloader_status()` where one bad downloader must not poison the whole Celery task

**Patterns:**
- When the failure should be retryable (Bindery queue rows with a transient error), the manager class returns `(False, msg)` and the calling pipeline keeps the item in `PostProcessing`. See `entities/managers.py:1138-1144` for the `transient_error_substring` detection
- When the failure is informational only (Bindery `manual-import/match` returning 409), the code logs and continues; the surrounding `try/except` re-raises nothing: `entities/managers.py:1330-1342`
- `entities/tasks.py` wraps the entire body of each `@shared_task` in a top-level `try/except Exception as e` so a single item failure doesn't crash the worker (see `entities/tasks.py:24-27, 218-219, 238-241, 472-473, 535-536`)

**Logging on errors:**
- `logger.error(...)` for unexpected failures with a short message and the exception
- `logger.warning(...)` for recoverable issues (no downloader configured, missing path, transient ban)
- `logger.info(...)` for normal operational milestones (item grabbed, item created, item imported)
- `logger.debug(...)` for verbose detail that becomes noisy in production

## Logging

**Framework:** stdlib `logging` (no loguru, no structlog)

**Patterns:**
- Module-level logger at the top of the file:
  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```
  Used in `entities/tasks.py:5-8`, `entities/management/commands/assign_missing_downloaders.py:16-18`, `entities/apps.py:2-4`, `entities/downloaders/airdcpp.py:2-6`, `entities/downloaders/qbittorrent.py:2-5`
- Lazy logger inside long functions (so the module doesn't import `logging` at top-level when the file already imports it elsewhere):
  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```
  Inside `Mylar3.poll()`, `Bindery.check_queue()`, `Bindery.post_process()`, `Arr.check_itemqueue()`, `Sonarr.post_process()` — see `entities/managers.py:56-57, 212-213, 478-479, 623, 1051-1052, 1112-1113, 1205-1209, 1366-1368, 1444-1446, 1477-1481`
- Configuration lives in `harpoon2/settings_template.py:196-250`:
  - `RotatingFileHandler` at `/var/log/harpoon2/django.log`, 10 MB × 5 backups
  - `StreamHandler` to stdout (container)
  - Verbose format: `{levelname} {asctime} {name} {message}`
  - Per-app loggers: `django` (INFO), `itemqueue` (DEBUG), `entities` (DEBUG), `celery` (DEBUG). Each logger has `propagate: False` and feeds both `console` and `file`
- Total log calls in project code: ~391 across the codebase (`logger.{info,debug,warning,error}` excluding `lib/rtorrent/`). Debug-level dominates, then info, then warning, then error
- `print(...)` is used only in scripts and standalone tools (`migrate_sqlite_to_postgres.py`, `check_item.py`, `test_scgi.py`) and in two debug calls in `harpoon2/views.py:83-85` (`DEBUG home: CALLED`) and `users/signals.py:13`. None of these are in production code paths

## Comments

**When to Comment:**
- Comments explain *why* rather than *what*. Examples:
  - `# Multi-file items (epub/mobi + cover art, etc.) must be staged as a whole folder...` — `entities/managers.py:1221-1224`
  - `# Bindery queue record (from /api/v1/queue): ...` — `entities/managers.py:1116-1118`
  - `# Preserve archived status - don't overwrite it during updates` — `entities/managers.py:64-65`
  - `# qBittorrent bans our IP...` — `entities/downloaders/qbittorrent.py:17-23`
- Comments are used to mark subtle decisions in long methods:
  - `# CRITICAL: Pre-calculate total sizes for each item BEFORE the loop` — `harpoon2/views.py:126-127`
  - `# In PostgreSQL, you can't call .filter() on a sliced QuerySet` — `harpoon2/views.py:886-887`
- `# TODO: ...` markers exist in three places (`entities/tasks.py:291`, `entities/tasks.py:343`, `entities/managers.py:1713` — see CONCERNS.md). No `FIXME`, `XXX`, or `HACK` markers
- Block comments at the top of each `models.py`, `tasks.py`, and most manager classes summarise the intent (see `entities/managers.py:1-3, 81-90, 110-120, 147-158, 469-471, 938-944, 1531-1533`)

**JSDoc/TSDoc:**
- Python docstrings (`"""..."""`) are used pervasively. Conventions:
  - `Args:` block listing parameters
  - `Returns:` block describing the tuple/dict/None
  - For Bool-tuple returns, both arms are documented (`Returns: (success: bool, message: str)`)
  - See `entities/downloaders/sabnzbd.py:81-93, 130-138, 248-253`, `entities/managers.py:108-120, 147-158, 729-737`, `entities/downloaders/airdcpp.py:114-128`
- Single-line docstrings appear on short helpers: `entities/managers.py:1316-1318` (`# Recoverable failure. Try manual-import/match first if the row is in...`), `entities/models.py:81-89`
- `entities/managers.py` contains the most comprehensive docstrings in the project — every public method on `Arr`, `Sonarr`, `Radarr`, `Lidarr`, `Readarr`, `Whisparr`, `Mylar3`, `Bindery`, and `Blackhole` is documented

## Function Design

**Size:**
- Functions in this codebase are large. `Bindery.post_process()` is ~170 lines, `Mylar3.post_process()` is ~210 lines, `Mylar3.poll()` is ~115 lines, `harpoon2/views.py:home()` is ~180 lines, `harpoon2/views.py:api_dashboard()` is ~180 lines. Helper functions are extracted for distinct steps (`Bindery._queue_record_for_item`, `Bindery._book_import_in_flight`, `Bindery._delete_stale_original_row`, `Bindery._manual_import_new`, `Bindery._detect_format`)
- Single-purpose helpers stay small: `find_rar_archives`, `find_zip_archives`, `Bindery._parse_path_remap`, `Bindery._detect_format`

**Parameters:**
- Manager client constructors take `manager` (the `entities.Manager` instance) as their only positional argument and pull URL/API key/label from it. See `Arr.__init__(self, manager)` at `entities/managers.py:5-12`, `Mylar3.__init__` at `entities/managers.py:472-477`, `Bindery.__init__` at `entities/managers.py:963-987`
- Downloader client constructors take `downloader` (the `entities.Downloader` instance) as their only positional argument. See `BaseDownloader.__init__(self, downloader=None)` at `entities/downloaders/base.py:7-21`. Each subclass then has `_init_client()` to read options and create the underlying client
- API methods take the smallest useful positional argument (`hash`, `info_hash`, `nzo_id`, `comic_search_name`, `path`) plus `**kwargs` for downloader-specific options (e.g. `SABnzbdDownloader.add(self, file_path: str, **kwargs)`, `RTorrentDownloader.add(self, file_path: str, **kwargs)`)

**Return Values:**
- Tuples `(success: bool, message: str)` are the standard for any external-call result. Used by every test, verify, cleanup, post-process, queue lookup, and manual-import path
- `list[dict]` for collection endpoints (`get_completed`, `get_files`, `get_active_downloads`)
- `dict` for single-item info (`get_download_info`, `get_status`)
- DB objects or `None` for `Item.objects.get_or_create`, `Downloader.objects.filter(...).first()`. The codebase never returns a `Manager.DoesNotExist`-style stub; it always returns `None` or lets the exception bubble

## Module Design

**Exports:**
- `entities/downloaders/__init__.py` declares `__all__` with both new (`RTorrentDownloader`) and legacy (`RTorrent`) names. Same for SABnzbd, AirDC++, QBittorrent (`entities/downloaders/__init__.py:7-17`)
- `entities/downloaders.py` (sibling to the package) is a thin re-export: `from .downloaders import RTorrent, SABNzbd` + `__all__ = ['RTorrent', 'SABNzbd']`. Older import paths still work
- `harpoon2/views.py` and `harpoon2/tasks.py` don't declare `__all__` — they import what they need and the modules are internal
- `entities/management/commands/assign_missing_downloaders.py` follows the Django convention: defines `class Command(BaseCommand)` with `add_arguments()` and `handle(self, *args, **options)`

**Barrel Files:**
- Not used. Each app exposes its models directly via `from .models import ...` rather than `from . import models` then `models.X`. The view modules are the only ones that use the latter pattern (`entities/views.py:8`, `users/views.py:6` — both for readability in class definitions)

**`app_name` namespaces:**
- Both apps register namespaces in `urls.py`: `app_name = 'entities'` (`entities/urls.py:4`), `app_name = 'users'` (`users/urls.py:4`). The root `harpoon2/urls.py` includes them with the namespace kwarg: `path('entities/', include('entities.urls', namespace='entities'))`, `path('users/', include('users.urls', namespace='users'))`. `dplibs/search.py` declares `app_name = 'watson'` (a leftover from when django-watson was used for search)

**Custom User model:**
- `AUTH_USER_MODEL = 'users.CustomUser'` (`harpoon2/settings_template.py:68`). The codebase always reaches for the user model via `from django.contrib.auth import get_user_model; User = get_user_model()` rather than importing `CustomUser` directly (see `harpoon2/views.py:8, 14`, `users/models.py:95-96`). Forms in `users/forms.py:6-22` reference `CustomUser` directly because Django's `UserCreationForm` / `UserChangeForm` require a concrete class

---

*Convention analysis: 2026-08-28*
