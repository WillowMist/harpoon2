# Testing Patterns

**Analysis Date:** 2026-08-28

## Test Framework

**Runner:**
- **Django's built-in test runner** (`python manage.py test`) is the framework. No pytest, no pytest-django, no nose2. Configuration is implicit via `manage.py` which calls `execute_from_command_line(sys.argv)`
- Each Django app has a `tests.py` stub that imports `from django.test import TestCase` and stops there:
  - `entities/tests.py:1` — `from django.test import TestCase` (3 lines total, no test cases)
  - `users/tests.py:1` — `from django.test import TestCase` (3 lines total, no test cases)
  - `itemqueue/tests.py:1` — `from django.test import TestCase` (3 lines total, no test cases)
- There are no `test_*.py` files, no `tests/` subdirectory inside any app, no `conftest.py`, no `pytest.ini`, no `tox.ini`, no `pyproject.toml`
- `requirements.txt` and `Pipfile` do not include pytest, coverage, factory_boy, faker, freezegun, responses, or any other test-time dependency. `Pipfile` `[dev-packages]` is empty
- The project ships a root-level smoke script, `test_scgi.py:1-5`, which is not a Django test but a manual probe:
  ```python
  from lib.rtorrent.lib.xmlrpc.clients.scgi import SCGIServerProxy
  client = SCGIServerProxy("scgi://127.0.0.1:5002/")
  result = client.system.api_version()
  print("API Version:", result)
  ```

**Run Commands:**
- No `Makefile`, no `noxfile.py`, no `taskfile.py`. The implied commands are:
  ```bash
  python manage.py test                              # Run all tests
  python manage.py test entities                     # One app
  python manage.py test entities.tests               # Single module
  python manage.py test --verbosity=2                # Verbose output
  ```
- There is no test stage in `.github/workflows/docker.yml` — the only CI step is `docker/build-push-action@v5` to publish the image on push to `master` or on `workflow_dispatch`
- There is no coverage configuration (`.coveragerc`, `coverage` in `requirements.txt`)

## Test File Organization

**Location:**
- Django's default per-app `tests.py` stub. No `tests/` subdirectories, no co-located `test_*.py` next to source files
- The repo currently has zero test cases — only three placeholder files. The convention *for new tests* is to extend `entities/tests.py`, `users/tests.py`, or `itemqueue/tests.py` (the file Django creates when you run `startapp`)

**Naming:**
- File names are Django defaults (`tests.py`)
- Class names must follow `django.test.TestCase` convention (`TestCase`, `*Test`, `*TestCase`). No examples exist in the repo yet
- Method names follow `def test_*` (Django's auto-discovery). No examples exist in the repo yet

**Structure:**
```
harpoon2/
├── entities/
│   └── tests.py          # stub, 3 lines
├── itemqueue/
│   └── tests.py          # stub, 3 lines
├── users/
│   └── tests.py          # stub, 3 lines
└── test_scgi.py          # manual probe, not a Django test
```

## Test Structure

**Suite Organization:**
- Django convention: `from django.test import TestCase` plus one class per scenario. There are no real examples to inspect, but the placeholders signal the intent:
  ```python
  # entities/tests.py
  from django.test import TestCase
  
  # Create your tests here.
  ```

**Patterns to use (per Django docs and project conventions):**
- For DB-touching tests, prefer `django.test.TestCase` (wraps each test in a transaction and rolls back). For pure logic that doesn't touch the DB, `unittest.TestCase` is fine
- For manager/downloader classes that read from `entities.models.Manager`, `entities.models.Downloader`, `itemqueue.models.Item`, etc., you need either fixtures or a `setUp` that creates `DownloadFolder`, `Manager`, `Downloader`, and `Seedbox` rows first (FK chain: `DownloadFolder` is referenced by `Manager.folder`, `Seedbox` is referenced by `Downloader.seedbox`, both `Manager` and `Downloader` are referenced by `Item`)
- The cleanest path is to use the `entities.models.Manager.from_db()` hook: it builds `manager.client` lazily, so any test that constructs a `Manager` instance in the DB must provide `managertype`, `name`, `url`, `apikey` so the cache initialiser succeeds
- For manager clients that hit external APIs (Sonarr, Radarr, Lidarr, Readarr, Whisparr, Bindery, Blackhole, Mylar3), wrap the test with `unittest.mock.patch` on `requests.get` / `requests.post` to avoid live HTTP calls

## Mocking

**Framework:**
- Standard library `unittest.mock` is the natural choice — it's available everywhere Python is, requires no install, and integrates cleanly with `django.test.TestCase`. The repo has zero current mock usage (`grep -rn "mock\|Mock\|@patch\|patch(" --include="*.py"` returns no matches), so any new mock code is greenfield

**Patterns (recommended):**
- For HTTP-external calls (`entities/managers.py`, `entities/tasks.py`, `harpoon2/views.py`):
  ```python
  from unittest.mock import patch, MagicMock
  
  @patch('entities.managers.requests.get')
  def test_arr_check_queue_returns_records(mock_get):
      mock_get.return_value.json.return_value = {'records': [...]}
      mock_get.return_value.status_code = 200
      
      mgr = Manager.objects.create(name='test', managertype='Sonarr', url='http://x', apikey='y')
      client = Sonarr(mgr)
      ok, dt = client.check_queue()
      
      self.assertTrue(ok)
      self.assertEqual(len(dt), 1)
  ```
- For Celery tasks that call `transfer_files_async.delay(...)` (see `harpoon2/views.py:457`), patch the task object directly:
  ```python
  @patch('itemqueue.tasks.transfer_files_async.delay')
  def test_retry_postprocessing_transfer(mock_delay):
      ...
  ```
- For SFTP/Paramiko operations (`itemqueue/tasks.py:transfer_files_async`), patch `paramiko.SSHClient` to return a mock with `open_sftp()` returning another mock

**What to Mock:**
- All `requests.*` calls in `entities/managers.py`, `entities/tasks.py`, `entities/management/commands/assign_missing_downloaders.py`, `harpoon2/views.py` (e.g. `api_version_check` at `harpoon2/views.py:1033`)
- Celery `task.delay(...)` to avoid running the queue
- `paramiko.SSHClient` and `paramiko.SSHClient.open_sftp()` in `itemqueue/tasks.py` and `entities/downloaders/sabnzbd.py:cleanup()`
- `subprocess.run` for the RAR/ZIP extraction paths (`itemqueue/tasks.py:extract_rar_archive`)
- File-system access via `tempfile.mkdtemp` and `tempfile.TemporaryDirectory`

**What NOT to Mock:**
- Pure-Python helpers like `Bindery._parse_path_remap` (`entities/managers.py:989-1008`), `Bindery._detect_format` (`entities/managers.py:1511-1528`), `Bindery.apply_path_remap` (`entities/managers.py:1010-1022`), `FileTransfer.percent_complete` (`itemqueue/models.py:64-67`) — these are best tested with real inputs
- `Notification._should_notify` (`users/models.py:115-136`) — pure mapping table; instantiate a `NotificationSettings` with explicit booleans

## Fixtures and Factories

**Test Data:**
- No fixtures directory exists. No `factory_boy` or `faker` is installed. Tests that need DB rows must build them inline in `setUp` or use Django's `fixtures` argument to `TestCase`
- Inline factories are the de facto style:
  ```python
  def setUp(self):
      self.folder = DownloadFolder.objects.create(folder='/tmp/dl')
      self.seedbox = Seedbox.objects.create(
          name='box', host='localhost', port=22, username='u'
      )
      self.downloader = Downloader.objects.create(
          name='sab', downloadertype='SABNzbd',
          seedbox=self.seedbox, options={}
      )
      self.manager = Manager.objects.create(
          name='sonarr-test', managertype='Sonarr',
          url='http://sonarr', apikey='abc',
          folder=self.folder,
      )
  ```

**Location:**
- No fixtures directory under any app. If fixtures are introduced later, the conventional Django location is `<app>/fixtures/<name>.json` or `<app>/fixtures/<name>.yaml`

## Coverage

**Requirements:** None enforced. No `coverage` package in `requirements.txt`, no `.coveragerc`, no CI gate. Adding coverage would require adding `coverage` to `requirements.txt` and either a `pytest-cov` workflow or `coverage run --source='entities,itemqueue,users' manage.py test && coverage report`

**View Coverage:**
```bash
# After installing coverage:
coverage run --source='entities,itemqueue,users,harpoon2' manage.py test
coverage report -m
coverage html  # writes htmlcov/
```

## Test Types

**Unit Tests:**
- The intended target. Most manager/downloader classes are pure-Python (`Bindery._parse_path_remap`, `Bindery._detect_format`, `Bindery.apply_path_remap`, `Mylar3.get_comic_with_issue` style logic) and can be unit-tested without DB or HTTP. Mocks for `requests.*` cover the rest
- `entities/downloaders/base.py:BaseDownloader.get_download_info` returns a default dict; subclass overrides live in `entities/downloaders/sabnzbd.py:346-371`, `entities/downloaders/qbittorrent.py:313-`, `entities/downloaders/airdcpp.py`. Each can be tested with a fake `hash` argument against a mocked client

**Integration Tests:**
- The codebase doesn't separate these. If introduced, they would live alongside the unit tests in the same `tests.py`
- End-to-end coverage of the transfer pipeline (`itemqueue/tasks.py:transfer_files_async`) is the natural integration target — it crosses SFTP → filesystem → RAR extraction → manager `post_process` → downloader `cleanup`

**E2E Tests:**
- **Not used.** No Selenium, Playwright, Cypress, or Django E2E framework is configured. AGENTS.md documents a manual workflow instead: `ssh docker` → `docker exec harpoon2-app python manage.py shell` for DB inspection (see AGENTS.md "Testing in Container"). This is the de facto UAT path

## Common Patterns

**Async Testing:**
- Celery tasks are decorated with `@shared_task` (`entities/tasks.py:14, 21, 222, 228, 244, 387, 476`; `harpoon2/tasks.py:6`; `itemqueue/tasks.py` has tasks defined as plain functions called from views via `.delay()` — see `harpoon2/views.py:457`). Test by:
  1. Calling the underlying function directly with `.apply().get()` to run synchronously, or
  2. Patching `task.delay` and asserting the call args
- The `.delay()` call inside `harpoon2/views.py:457` (`retry_postprocessing_transfer`) is the only direct invocation from a view; everything else flows through `entities.tasks.*` via Celery Beat

**Error Testing:**
- The codebase's dominant error contract is `(success: bool, message: str)`. Test both arms:
  ```python
  ok, msg = client.test()
  self.assertFalse(ok)
  self.assertIn('HTTP 401', msg)
  ```
- For `Bindery.post_process()` specifically, exercise the four exits: success path, "Bindery already imported", "queue status needs no manual-import", and "Bindery has an import in flight for {name}" — each returns `(True, msg)` with a distinct message string the caller pattern-matches on

**Migration Testing:**
- The repo has 18 entity migrations (`entities/migrations/0001_initial.py` through `0018_manager_bindery_fields.py`) and 3 user migrations. None of them include test coverage. If you add a migration that has a `RunPython` data step (see `0018_manager_bindery_fields.py:13-47` as the only example), write a forward test that creates a Bindery manager with `options={'ebook_folder': '/x'}` and asserts the data migration moves the value to `bindery_ebook_folder`

**No Existing Patterns:**
- The codebase has **no test cases at all** today. Every recommendation in this document is inferred from the Django conventions + the project's own source shape. Any new test file should be added by extending `entities/tests.py`, `users/tests.py`, or `itemqueue/tests.py` rather than introducing a new layout

---

*Testing analysis: 2026-08-28*
