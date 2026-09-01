"""Lock-in test for the celery worker --concurrency and --prefetch-multiplier.

Phase 5 bounds the worker fork count to match the Postgres pool math
(4 worker forks + 6 gunicorn + ~2 transient = 12 of 100 connections).
--concurrency=4 is the recommended value for a single-container host;
the test allows 1..16 so the operator can dial up on a larger PG pool
without a test failure. --prefetch-multiplier=1 matches
harpoon2/celery.py worker_prefetch_multiplier=1.

Also guards --max-tasks-per-child=10 (commit 230b74e) so the fork-recycle
regression guard stays intact.
"""
import re
from pathlib import Path

SUPERVISORD_PATH = Path(__file__).parent.parent / "supervisord.conf"


def _celery_worker_command() -> str:
    content = SUPERVISORD_PATH.read_text()
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == "[program:celery-worker]"
            continue
        if in_section and stripped.startswith("command="):
            return stripped[len("command="):]
    raise AssertionError(
        f"Could not find celery-worker command in {SUPERVISORD_PATH}"
    )


def test_celery_worker_has_concurrency_flag():
    """The celery-worker command must set --concurrency= explicitly (NOT the
    default CPU-bound value) so the fork count matches the PG pool."""
    worker_command = _celery_worker_command()
    assert "--concurrency" in worker_command, (
        f"celery-worker command missing --concurrency: {worker_command!r}\n"
        f"Without an explicit cap, Celery defaults to CPU count, which can "
        f"auto-scale past the Postgres pool on bigger hosts."
    )


def test_celery_worker_concurrency_in_bounded_range():
    """--concurrency must be in [1, 16] — bounded enough to protect a
    100-connection pool while allowing operator adjustment on larger hosts."""
    worker_command = _celery_worker_command()
    m = re.search(r"--concurrency[=\s]+(\d+)", worker_command)
    assert m, f"could not parse --concurrency value from {worker_command!r}"
    value = int(m.group(1))
    assert 1 <= value <= 16, (
        f"--concurrency={value} is outside the bounded range [1, 16]"
    )


def test_celery_worker_has_prefetch_multiplier_one():
    """--prefetch-multiplier=1 must be present, matching
    harpoon2/celery.py worker_prefetch_multiplier=1."""
    worker_command = _celery_worker_command()
    m = re.search(r"--prefetch-multiplier[=\s]+(\d+)", worker_command)
    assert m, (
        f"celery-worker command missing --prefetch-multiplier: {worker_command!r}"
    )
    assert int(m.group(1)) == 1, (
        f"--prefetch-multiplier must be 1, got {m.group(1)}"
    )


def test_celery_worker_preserves_max_tasks_per_child():
    """--max-tasks-per-child=10 (commit 230b74e) must be preserved — the
    fork-recycle guard that prevents connection accumulation."""
    worker_command = _celery_worker_command()
    m = re.search(r"--max-tasks-per-child[=\s]+(\d+)", worker_command)
    assert m, (
        f"celery-worker command missing --max-tasks-per-child: {worker_command!r}"
    )
    assert int(m.group(1)) == 10, (
        f"--max-tasks-per-child must stay 10, got {m.group(1)}"
    )