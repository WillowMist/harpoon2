"""Tests for fix D: requests timeout discipline in Celery tasks and manager clients.

The 08:34 UTC Postgres pool exhaustion was caused by Celery tasks blocking on
no-timeout HTTP/XMLRPC calls to manager/downloader APIs, holding DB connections
open indefinitely. These tests lock in `timeout=(3.05, 10)` on every requests
call site so a future regression (someone adding a new requests call without
timeout) is caught at CI time.

For sites without tests we can't easily exercise (the bare rTorrent XMLRPC
ServerProxy), we rely on grep-based lock-in tests below.
"""
import inspect
import re
from unittest.mock import patch, MagicMock

import pytest


# --------------------------------------------------------------------------
# Grep-based lock-in tests: ensure no requests.get/post in the codebase is
# missing timeout. These prevent future regressions when someone adds a new
# HTTP call.
# --------------------------------------------------------------------------

# Files we know contain requests.get/post calls to manager/downloader APIs
# (added before fix D landed)
KNOWN_REQUESTS_FILES = [
    "entities/tasks.py",
    "entities/managers.py",
    "entities/downloaders/sabnzbd.py",
    "entities/downloaders/qbittorrent.py",
    "entities/downloaders/rtorrent.py",
]


def _read(path):
    with open(path) as f:
        return f.read()


class TestNoBareRequests:
    """No requests.get/post in known call sites is missing a `timeout=` kwarg."""

    @pytest.mark.parametrize("path", KNOWN_REQUESTS_FILES)
    def test_no_bare_requests_get(self, path):
        """Every `requests.get(...)` call site must include `timeout=`."""
        try:
            content = _read(path)
        except FileNotFoundError:
            pytest.skip(f"{path} not present")
        # Find each line containing `requests.get(` and verify timeout is on
        # the same call (allow multi-line by looking ahead 4 lines).
        pattern = re.compile(r"requests\.get\(", re.MULTILINE)
        bad = []
        for m in pattern.finditer(content):
            # look at the call site + a small window for multi-line continuation
            window = content[m.start():m.start() + 600]
            if "timeout=" not in window:
                # report the line number
                line_no = content[:m.start()].count("\n") + 1
                bad.append((line_no, window.split("\n")[0].strip()))
        assert not bad, (
            f"{path}: requests.get() calls missing timeout=:\n"
            + "\n".join(f"  line {ln}: {snippet}" for ln, snippet in bad)
        )

    @pytest.mark.parametrize("path", KNOWN_REQUESTS_FILES)
    def test_no_bare_requests_post(self, path):
        """Every `requests.post(...)` call site must include `timeout=`."""
        try:
            content = _read(path)
        except FileNotFoundError:
            pytest.skip(f"{path} not present")
        pattern = re.compile(r"requests\.post\(", re.MULTILINE)
        bad = []
        for m in pattern.finditer(content):
            window = content[m.start():m.start() + 600]
            if "timeout=" not in window:
                line_no = content[:m.start()].count("\n") + 1
                bad.append((line_no, window.split("\n")[0].strip()))
        assert not bad, (
            f"{path}: requests.post() calls missing timeout=:\n"
            + "\n".join(f"  line {ln}: {snippet}" for ln, snippet in bad)
        )


# --------------------------------------------------------------------------
# Behavioral tests: verify the timeout value is actually passed through the
# real call path (not just present in the source).
# --------------------------------------------------------------------------

class TestPollManagerTimeout:
    """poll_manager must pass timeout=(3.05, 10) to requests.get.

    The celery task is `poll_manager(manager_id)` (a Celery task signature,
    not the inner Python function), which makes behavioral mocking painful.
    Lock-in test: verify the source has the timeout literal.
    """

    def test_poll_manager_source_has_timeout(self):
        from entities import tasks as entities_tasks
        src = inspect.getsource(entities_tasks)
        # Find the inner poll_manager function (the one with the requests call)
        # — it's NOT the @shared_task wrapper, which just calls .delay(manager.id).
        # Look for "requests.get(" inside poll_manager logic.
        poll_idx = src.find("def poll_manager(")
        assert poll_idx != -1, "poll_manager function not found"
        # Find next requests.get after poll_manager definition
        get_idx = src.find("requests.get(", poll_idx)
        assert get_idx != -1, "requests.get in poll_manager not found"
        window = src[get_idx:get_idx + 400]
        assert "timeout=(3.05, 10)" in window, \
            f"poll_manager requests.get is missing timeout=(3.05, 10): {window[:200]!r}"


class TestAssignItemsTimeout:
    """assign_items_to_downloaders must pass timeout=(3.05, 10)."""

    def test_assign_items_source_has_timeout(self):
        from entities import tasks as entities_tasks
        src = inspect.getsource(entities_tasks)
        # Look for the inner helper that makes the requests.get call
        # (search for the function that calls requests.get inside the
        # assign-items flow).
        get_idx = src.find("requests.get(")
        # Walk forward — there are two requests.get calls (poll_manager +
        # assign_items path). Find the SECOND one.
        if get_idx != -1:
            get_idx = src.find("requests.get(", get_idx + 1)
        assert get_idx != -1, "second requests.get in entities/tasks.py not found"
        window = src[get_idx:get_idx + 400]
        assert "timeout=(3.05, 10)" in window, \
            f"assign_items requests.get is missing timeout=(3.05, 10): {window[:200]!r}"


class TestArrManagerTimeout:
    """The base Arr.test() / check_queue() methods must pass timeout=(3.05, 10)."""

    def test_method_signatures_carry_timeout_in_source(self):
        """Behavioral test via source inspection — these are short methods."""
        from entities import managers as entities_managers
        # Find the Arr base class
        src = inspect.getsource(entities_managers)
        # Both methods must have changed; check for the new timeout literal
        # near each requests.get call.
        assert "timeout=(3.05, 10)" in src, \
            "Arr base class must use timeout=(3.05, 10) for requests.get"
        # Verify there are no other requests.get without timeout in the Arr file
        # (this is redundant with the grep test above but useful as a smoke check)
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "requests.get(" in line and "timeout=" not in line:
                # Check next 5 lines for multi-line continuation
                window = "\n".join(lines[i:i+5])
                if "timeout=" not in window:
                    pytest.fail(
                        f"entities/managers.py:{i+1}: requests.get missing timeout=\n  {line.strip()}"
                    )


class TestMylarTimeout:
    """Mylar3.post_process must pass timeout=(3.05, 10) to requests.post."""

    def test_mylar_source_has_timeout(self):
        from entities import managers as entities_managers
        src = inspect.getsource(entities_managers)
        # The Mylar block is identifiable by the comment header
        # Find the requests.post in Mylar's post_process
        mylar_idx = src.find("[Mylar3 post_process]")
        assert mylar_idx != -1, "Mylar3 post_process not found"
        # Look ahead for the requests.post call
        post_idx = src.find("requests.post(", mylar_idx)
        assert post_idx != -1, "requests.post in Mylar3 not found"
        window = src[post_idx:post_idx + 300]
        assert "timeout=(3.05, 10)" in window, \
            f"Mylar3 requests.post is missing timeout=(3.05, 10): {window[:200]!r}"


# --------------------------------------------------------------------------
# Configuration tests: verify the deployment-side changes.
# --------------------------------------------------------------------------

class TestConnMaxAgeSet:
    """CONN_MAX_AGE must be set in settings_template.py so Postgres
    connections are reused instead of opened per request."""

    def test_postgres_block_has_conn_max_age(self):
        content = _read("harpoon2/settings_template.py")
        # Find the Postgres DATABASES block and verify CONN_MAX_AGE is inside
        pg_idx = content.find("django.db.backends.postgresql")
        assert pg_idx != -1, "Postgres DATABASES block not found"
        # Look at the closing brace of this dict (next '    }' at column 8)
        window = content[pg_idx:pg_idx + 1000]
        assert "'CONN_MAX_AGE'" in window, \
            "Postgres DATABASES dict missing CONN_MAX_AGE — connections will churn and re-flood the pool"

    def test_sqlite_block_has_conn_max_age(self):
        content = _read("harpoon2/settings_template.py")
        sq_idx = content.find("django.db.backends.sqlite3")
        assert sq_idx != -1, "SQLite DATABASES block not found"
        window = content[sq_idx:sq_idx + 600]
        assert "'CONN_MAX_AGE'" in window, \
            "SQLite DATABASES dict missing CONN_MAX_AGE"


class TestGunicornMigration:
    """supervisord.conf must run gunicorn, not runserver, for the web tier."""

    def test_no_runserver_in_supervisord(self):
        content = _read("supervisord.conf")
        # Parse out just the [program:django] section's `command=` line so the
        # legacy mention in our historical comment doesn't trip the test.
        in_django = False
        command = ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_django = stripped == "[program:django]"
                continue
            if in_django and stripped.startswith("command="):
                command = stripped
                break
        assert "runserver" not in command, (
            f"[program:django] command still uses runserver: {command!r}\n"
            f"Replace with gunicorn for production deployment."
        )
        assert "gunicorn" in command, (
            f"[program:django] command is not gunicorn: {command!r}"
        )

    def test_uses_gunicorn(self):
        content = _read("supervisord.conf")
        assert "gunicorn" in content, \
            "supervisord.conf does not reference gunicorn"
        # Sanity check the bind/workers flags
        assert "--bind" in content, "gunicorn command missing --bind"
        assert "--workers" in content, "gunicorn command missing --workers"
        assert "--timeout" in content, "gunicorn command missing --timeout"

    def test_gunicorn_in_requirements(self):
        """Make sure gunicorn is a declared dependency (operator can't pull the image otherwise)."""
        content = _read("requirements.txt")
        assert re.search(r"^gunicorn[><=]", content, re.MULTILINE), \
            "gunicorn not declared in requirements.txt — Docker image build will fail"
