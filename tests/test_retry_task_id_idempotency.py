"""PIPE-08 lock-in test for the deterministic retry task_id.

retry_postprocessing must call apply_async with the deterministic
task_id format `retry-{item_hash}-{attempt}` (or `next_attempt`) so the
Redis broker can dedup re-deliveries of the same task id. The attempt
count must also flow through the dispatch args so the next invocation
knows which attempt it is (and can enforce the PIPE-01 cap).

Per RESEARCH.md §Pitfall 4: broker dedup alone is not sufficient — the
task body's last_recovery_at check is the application-side safety net.
This test locks in the broker-side half of the contract.
"""
import inspect

from itemqueue.tasks import retry_postprocessing


def test_deterministic_task_id_format_in_source():
    """PIPE-08: the apply_async call must use the deterministic task_id
    format `retry-{item_hash}-{attempt}` (or `next_attempt`)."""
    src = inspect.getsource(retry_postprocessing)
    assert (
        'task_id=f"retry-{item_hash}-{attempt}"' in src
        or 'task_id=f"retry-{item_hash}-{next_attempt}"' in src
    ), (
        "retry_postprocessing must call apply_async with the deterministic "
        "task_id format `retry-{item_hash}-{attempt}` for PIPE-08 broker dedup. "
        "Source lacks the format string."
    )


def test_attempt_count_passed_in_dispatch_args():
    """The attempt count must be part of the apply_async args so the next
    invocation knows which attempt it is (and can enforce the cap)."""
    src = inspect.getsource(retry_postprocessing)
    assert (
        "args=[item_hash, attempt]" in src
        or "args=[item_hash, next_attempt]" in src
    ), (
        "retry_postprocessing must pass the attempt count in apply_async args "
        "so the next invocation can enforce the PIPE-01 cap."
    )


def test_countdown_present():
    """A retry cadence (countdown=) must be present on the apply_async call.
    300 or 600 are both valid retry cadences."""
    src = inspect.getsource(retry_postprocessing)
    assert "countdown=" in src, (
        "retry_postprocessing must schedule the next retry with a countdown "
        "(300 or 600 — either is a valid retry cadence)."
    )