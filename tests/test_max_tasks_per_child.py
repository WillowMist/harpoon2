"""Lock-in test for the celery worker --max-tasks-per-child setting.

The recovery-loop churn (each transfer_files_async fork holding a
Postgres connection) accumulated forks over ~50 minutes until gunicorn
couldn't get a connection within the 5s healthcheck window. Setting
--max-tasks-per-child=10 forces each fork to recycle after 10 tasks,
bounding the connection-pool accumulation.

This test verifies the supervisord.conf command includes the flag.
"""
import re
from pathlib import Path


def test_celery_worker_has_max_tasks_per_child():
    """The celery-worker program in supervisord.conf must set
    --max-tasks-per-child so forks recycle and don't accumulate
    Postgres connections across long recovery-loop churn."""
    supervisord_path = Path(__file__).parent.parent / "supervisord.conf"
    content = supervisord_path.read_text()

    # Find the [program:celery-worker] section
    in_section = False
    worker_command = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == "[program:celery-worker]"
            continue
        if in_section and stripped.startswith("command="):
            worker_command = stripped[len("command="):]
            break

    assert worker_command is not None, (
        f"Could not find celery-worker command in {supervisord_path}"
    )
    assert "--max-tasks-per-child" in worker_command, (
        f"celery-worker command missing --max-tasks-per-child: {worker_command!r}\n"
        f"Without it, transfer_files_async forks accumulate and starve "
        f"gunicorn of Postgres connections after ~50 min of recovery churn."
    )

    # Sanity: the value should be a small positive integer (5-100 range).
    # Too high = back to accumulating; too low = unnecessary churn overhead.
    m = re.search(r"--max-tasks-per-child[=\s]+(\d+)", worker_command)
    assert m, f"could not parse --max-tasks-per-child value from {worker_command!r}"
    value = int(m.group(1))
    assert 5 <= value <= 100, (
        f"--max-tasks-per-child={value} is outside the reasonable range [5, 100]"
    )
