---
phase: 05-pipeline-hardening
plan: 02
subsystem: infra
tags: [tenacity, retry, backoff, paramiko, sftp, requests, pipe-07]

# Dependency graph
requires: []
provides:
  - "dplibs/retry.py — reusable tenacity retry decorator factory (api_retry / sftp_retry / _sftp_connect_with_retry)"
  - "PIPE-07: Manager.test(), poll_manager(), and the SFTP connect path retry transient ConnectionError / Timeout / SSHException with exponential backoff"
affects: [05-pipeline-hardening plans 3-5 (row locking, recovery state machine, bounded concurrency)]

# Actuals (#2632) — pairs with the plan's `estimate` to calibrate future estimates.
actuals:
  tokens: 5122
  tasks: 3
  commits: 4

# Tech tracking
tech-stack:
  added: [tenacity (already pinned >=9.1,<10 — no new install)]
  patterns:
    - "Tenacity retry decorator factory in dplibs/retry.py: stop_after_attempt(5), wait_random_exponential(multiplier=1, max=60), retry_if_exception_type(...), reraise=True"
    - "Per-call retry (decorator on the network call) instead of Celery autoretry_for (whole-task re-run)"

key-files:
  created:
    - dplibs/retry.py
    - tests/test_tenacity_retry_manager.py
    - tests/test_tenacity_retry_sftp.py
    - tests/test_dplibs_retry_module.py
  modified:
    - entities/managers.py
    - entities/tasks.py
    - itemqueue/tasks.py
    - entities/downloaders/sabnzbd.py

key-decisions:
  - "Retry decorators live in a reusable dplibs/retry.py factory module (implicit namespace package, no __init__.py) so Plans 3+ can add more retry sites without duplicating the tenacity config."
  - "Bindery.test() retries via a new Bindery._api_get() helper (same pattern as Arr._api_get) rather than decorating the whole method — decorating the method is ineffective because its internal except Exception swallows the ConnectionError before tenacity sees it."
  - "RSA key loading in _sftp_connect_with_retry is version-compatible: tries paramiko.RSAKey.from_private_key_string (paramiko <5.0) and falls back to from_private_key(io.StringIO(...)) (paramiko >=5.0 removed the string API)."
  - "Tests neutralize tenacity's real backoff by patching time.sleep via a no_backoff fixture (tenacity.nap.sleep delegates to time.sleep), keeping the suite deterministic and fast."

patterns-established:
  - "Pattern: tenacity retry decorator factory (api_retry / sftp_retry) with reraise=True so the caller sees the original exception, not tenacity.RetryError."
  - "Pattern: extract the network call into a decorated _api_get helper so the operator-visible (True/False, msg) contract is preserved after retry exhaustion."

requirements-completed: [PIPE-07]

# Coverage metadata (#1602) — one entry per shipped deliverable.
coverage:
  - id: D1
    description: "dplibs/retry.py module with api_retry(), sftp_retry(), and _sftp_connect_with_retry(seedbox) exports; tenacity knobs pinned in source"
    requirement: PIPE-07
    verification:
      - kind: unit
        ref: "tests/test_dplibs_retry_module.py#test_module_exports_are_callable"
        status: pass
      - kind: unit
        ref: "tests/test_dplibs_retry_module.py#test_source_pins_tenacity_knobs"
        status: pass
    human_judgment: false
  - id: D2
    description: "Arr.test() retries transient ConnectionErrors via the api_retry-wrapped _api_get helper; (True/False, msg) contract preserved after exhaustion"
    requirement: PIPE-07
    verification:
      - kind: unit
        ref: "tests/test_tenacity_retry_manager.py#test_arr_test_retries_on_connection_error"
        status: pass
      - kind: unit
        ref: "tests/test_tenacity_retry_manager.py#test_arr_test_reraises_original_exception_after_exhaustion"
        status: pass
    human_judgment: false
  - id: D3
    description: "Bindery.test() retries transient ConnectionErrors via the api_retry-wrapped _api_get helper; 401-arm and (True/False, msg) contract preserved"
    requirement: PIPE-07
    verification:
      - kind: unit
        ref: "tests/test_tenacity_retry_manager.py#test_bindery_test_retries_on_connection_error"
        status: pass
    human_judgment: false
  - id: D4
    description: "poll_manager() requests.get wrapped with the api_retry inline factory; timeout=(3.05, 10) discipline preserved"
    requirement: PIPE-07
    verification:
      - kind: unit
        ref: "tests/test_requests_timeout_discipline.py#TestPollManagerTimeout.test_poll_manager_source_has_timeout"
        status: pass
    human_judgment: false
  - id: D5
    description: "SFTP connect path (transfer_files_async + SABnzbd cleanup) uses _sftp_connect_with_retry; transient SSHExceptions absorbed, original exception propagates after exhaustion"
    requirement: PIPE-07
    verification:
      - kind: unit
        ref: "tests/test_tenacity_retry_sftp.py#test_sftp_connect_retries_on_ssh_exception"
        status: pass
      - kind: unit
        ref: "tests/test_tenacity_retry_sftp.py#test_sftp_connect_reraises_original_exception_after_exhaustion"
        status: pass
    human_judgment: false

# Metrics
duration: 45min
completed: 2026-09-01
status: complete
---

# Phase 5 Plan 02: Tenacity Retry Decorator on Manager API + SFTP Connect Summary

**Tenacity retry decorator factory in dplibs/retry.py wrapping Manager.test(), poll_manager(), and both SFTP connect sites with 5-attempt exponential backoff (reraise=True), closing PIPE-07**

## Performance

- **Duration:** 45 min
- **Started:** 2026-09-01T01:10:00Z
- **Completed:** 2026-09-01T01:55:00Z
- **Tasks:** 3
- **Files modified:** 8 (4 new, 4 modified)

## Accomplishments
- New `dplibs/retry.py` module exporting `api_retry()`, `sftp_retry()`, and `_sftp_connect_with_retry(seedbox)` — a reusable tenacity decorator factory (implicit namespace package, no `__init__.py`, same convention as `dplibs/session.py` / `dplibs/search.py` / `dplibs/filesystem.py`).
- `Arr.test()` refactored to delegate to a new `Arr._api_get()` decorated with `@api_retry()`; the `(True, dt)` / `(False, msg)` return contract is preserved.
- `Bindery.test()` retries via a new `Bindery._api_get()` helper (same pattern); the 401-arm and `(False, msg)` contract are preserved.
- `poll_manager()`'s `requests.get` wrapped with the `api_retry()` inline factory; `timeout=(3.05, 10)` discipline preserved (pinned by `tests/test_requests_timeout_discipline.py`).
- Both SFTP connect sites (`transfer_files_async` at `itemqueue/tasks.py` and the SABnzbd cleanup path) now call `_sftp_connect_with_retry(seedbox)` instead of inlining `paramiko.SSHClient()` + `ssh.connect(...)`.
- Three new test files (10 tests) covering happy-path retry-then-success and exhaustion-raises-original-exception for both the Manager API and SFTP connect paths, plus a module-shape lock-in test.
- Full suite: 77 passed (67 pre-existing + 10 new), including `test_sftp_watchdog.py` and `test_requests_timeout_discipline.py`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Create dplibs/retry.py + wrap Arr.test()** - `baa7a81` (feat)
2. **Task 2: Apply tenacity to Bindery.test + poll_manager + SFTP connect sites** - `9040d51` (feat)
3. **Task 3: Tenacity behavioral + lock-in tests** - `8f2b81d` (test)

**Plan metadata:** `docs(05-02): complete tenacity retry plan` (this SUMMARY)

## Files Created/Modified
- `dplibs/retry.py` - New tenacity retry decorator factory module: `api_retry()`, `sftp_retry()`, `_sftp_connect_with_retry(seedbox)`, `_load_rsa_key()`, `_API_RETRY_EXC`, `_SFTP_RETRY_EXC`.
- `entities/managers.py` - `Arr._api_get()` (new, `@api_retry()`-decorated) + `Arr.test()` delegates to it; `Bindery._api_get()` (new, `@api_retry()`-decorated) + `Bindery.test()` delegates to it.
- `entities/tasks.py` - `poll_manager()` requests.get wrapped via `api_retry()(lambda: requests.get(...))()` inline factory; `from dplibs.retry import api_retry` added.
- `itemqueue/tasks.py` - Inline SFTP connect block replaced with `ssh = _sftp_connect_with_retry(seedbox)`; `from dplibs.retry import _sftp_connect_with_retry` added.
- `entities/downloaders/sabnzbd.py` - Inline SFTP cleanup connect replaced with `_sftp_connect_with_retry(seedbox)`; `import paramiko` promoted to module top; `from dplibs.retry import _sftp_connect_with_retry` added.
- `tests/test_tenacity_retry_manager.py` - New: Arr.test + Bindery.test retry behavior (5-attempt budget, exhaustion contract).
- `tests/test_tenacity_retry_sftp.py` - New: `_sftp_connect_with_retry` retry behavior on SSHException.
- `tests/test_dplibs_retry_module.py` - New: module-shape lock-in (exports, tenacity-knob literals, signature).

## Decisions Made
- **Reusable factory module**: retry decorators live in `dplibs/retry.py` (not inline per call site) so Plans 3+ can add more retry sites without duplicating the tenacity config.
- **`_api_get` extraction for Bindery**: the plan's "decorate the whole `Bindery.test()` method" approach is ineffective — the method's internal `except Exception` swallows the ConnectionError and returns `(False, msg)`, so tenacity never sees an exception to retry. Extracted `Bindery._api_get()` (same pattern as `Arr._api_get`) so the retry wraps the network call and the `(False, msg)` contract survives exhaustion.
- **Version-compatible RSA key loading**: `paramiko.RSAKey.from_private_key_string` was removed in paramiko 5.0; `_load_rsa_key()` tries it first (paramiko <5.0) and falls back to `from_private_key(io.StringIO(...))` (works on all supported versions).
- **`no_backoff` test fixture**: tenacity's real exponential backoff is neutralized by patching `time.sleep` (tenacity.nap.sleep delegates to it) so the retry tests are deterministic and fast (~0.13s for all 10).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `stop_after_attempt(5)` caps at 5 total attempts, not "5 retries + 1 success"**
- **Found during:** Task 3 (test authoring)
- **Issue:** The plan's test spec asserted "call count is 6 (5 fails + 1 success)". Verified against tenacity 9.1.4: `stop_after_attempt(5)` stops when `attempt_number >= 5`, so the function is called at most 5 times — a 6th success attempt is impossible. The plan's decorator config (AC-2), RESEARCH.md, PATTERNS.md, and threat model all consistently pin 5 total attempts.
- **Fix:** Success tests fail the callback/connect 4 times and succeed on the 5th (call count 5). Exhaustion tests use 5 failures → original exception propagates. The behavioral contract (transient failures absorbed → success; exhaustion → original exception, not RetryError) is unchanged.
- **Files modified:** tests/test_tenacity_retry_manager.py, tests/test_tenacity_retry_sftp.py
- **Verification:** All 10 new tests pass.
- **Committed in:** 8f2b81d (part of task commit)

**2. [Rule 1 - Bug] `@responses.activate` stacked on `@patch('time.sleep')` breaks pytest fixture injection**
- **Found during:** Task 3 (first test run)
- **Issue:** With `@responses.activate` above `@patch('time.sleep')`, the responses wrapper hides the patched signature, so pytest binds the `arr` fixture to the sleep mock and the real fixture value is lost (ValueError on unpack).
- **Fix:** Replaced the `@patch('time.sleep')` decorator with a `no_backoff` fixture that patches `time.sleep` via a context manager for the test duration.
- **Files modified:** tests/test_tenacity_retry_manager.py, tests/test_tenacity_retry_sftp.py
- **Verification:** All 10 new tests pass.
- **Committed in:** 8f2b81d (part of task commit)

**3. [Rule 1 - Bug] `Bindery.test()` whole-method decoration is ineffective**
- **Found during:** Task 3 (Bindery test failed: `success is False`)
- **Issue:** The plan's Task 2 AC-1 required `@api_retry()` directly on `Bindery.test()`. But the method's internal `except Exception` catches the ConnectionError and returns `(False, str(e))` — a normal return, not an exception — so tenacity never retries. The retry decorator was a no-op.
- **Fix:** Extracted `Bindery._api_get()` (decorated with `@api_retry()`, same pattern as `Arr._api_get`) and had `test()` delegate to it. The 401-arm and `(False, msg)` contract are preserved; the callers at `entities/views.py:198` and `entities/tasks.py:276` still receive a 2-tuple.
- **Files modified:** entities/managers.py
- **Verification:** `test_bindery_test_retries_on_connection_error` passes; full suite green.
- **Committed in:** 8f2b81d (part of task commit)

**4. [Rule 1 - Bug] `paramiko.RSAKey.from_private_key_string` removed in paramiko 5.0**
- **Found during:** Task 1 (module authoring)
- **Issue:** The plan's helper spec (and the existing code at `itemqueue/tasks.py:471,968`) uses `paramiko.RSAKey.from_private_key_string(seedbox.ssh_key)`, but paramiko 5.0.0 (current, per `requirements.txt` `>=3.4,<6.0`) removed that API — it would raise AttributeError on the key-auth path.
- **Fix:** Added `_load_rsa_key()` which tries `from_private_key_string` first (paramiko <5.0) and falls back to `from_private_key(io.StringIO(...))` (works on all supported versions). Verified with a real generated RSA key round-trip.
- **Files modified:** dplibs/retry.py
- **Verification:** Key round-trip test passes; password-auth path verified with mocked SSHClient.
- **Committed in:** baa7a81 (part of task commit)

**5. [Rule 1 - Bug] Plan's Task 3C attribute assertion is wrong for tenacity 9.1.4**
- **Found during:** Task 3 (module-shape test authoring)
- **Issue:** The plan said the decorator factories "return objects with `stop`, `wait`, `retry`, `reraise` attributes". Verified: `retry(...)` returns a plain decorator function; the wrapped callable exposes `retry`, `retry_with`, and `statistics` (not `stop`/`wait`/`reraise`).
- **Fix:** The module-shape test asserts the real tenacity API: the factory returns a working decorator, and the wrapped callable has `retry` and `statistics`. The tenacity-knob literals are still pinned via source inspection.
- **Files modified:** tests/test_dplibs_retry_module.py
- **Verification:** `test_factories_return_working_decorators` passes.
- **Committed in:** 8f2b81d (part of task commit)

**6. [Rule 1 - Bug] Plan's Task 2 verification referenced a non-existent `cleanup_sftp` symbol**
- **Found during:** Task 2 (verification)
- **Issue:** The plan's verify command `from entities.downloaders.sabnzbd import cleanup_sftp` fails — the method is `SABnzbdDownloader.cleanup`, not a module-level `cleanup_sftp`.
- **Fix:** Verified with the correct import (`SABnzbdDownloader`) and source-inspected `cleanup` for `_sftp_connect_with_retry`.
- **Files modified:** none (verification only)
- **Verification:** Import + source-inspection check passes.
- **Committed in:** n/a (verification-only)

---

**Total deviations:** 6 auto-fixed (all Rule 1 — bugs in the plan's test specs / API assumptions)
**Impact on plan:** All auto-fixes were necessary for correctness. The behavioral contracts in the must_haves (transient failures absorbed → success; exhaustion → original exception propagates, not RetryError; `(False, msg)` contract preserved) are all satisfied. No scope creep.

## Issues Encountered
- The container (`harpoon2-app`) is not currently running, so the plan's `docker exec` verification commands could not be executed; equivalent local verification was performed with `DJANGO_SETTINGS_MODULE=harpoon2.settings` and the pytest suite.
- `paramiko.RSAKey.from_private_key_string` removal in paramiko 5.0 is a pre-existing latent issue at `itemqueue/tasks.py:968` (the per-file retry reconnect path, out of this plan's scope). The new `_sftp_connect_with_retry` helper is version-compatible, but the line-968 reconnect still uses the removed API and will fail on paramiko >=5.0 for key-auth seedboxes. Flagged for a future plan.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- PIPE-07 is closed: Manager.test(), poll_manager(), and both SFTP connect sites retry transient network failures with a bounded 5-attempt exponential backoff.
- `dplibs/retry.py` is the reusable factory Plans 3+ can extend for additional retry sites.
- The pre-existing `from_private_key_string` usage at `itemqueue/tasks.py:968` (per-file reconnect) should be migrated to `_load_rsa_key` in a future plan.

---
*Phase: 05-pipeline-hardening*
*Completed: 2026-09-01*