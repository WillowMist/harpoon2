r"""Static assertion: check_stalled_transfers has exactly one `except Exception`
block per try in the PostProcessing queue-retry branch (COR-04).

This test reads itemqueue/tasks.py, locates the queue-retry try block (the
one whose body calls `transfer_files_async.delay(item.hash)`), and asserts
that the matched block contains exactly one `except Exception` line. Before
COR-04 the file had a duplicate `except Exception` block at lines 1264-1265
— unreachable per Python syntax (a `try` block can only have one matching
exception handler at the same level), but visually noisy and confusing.

The regex bounds the match from `try:` through the next `\n<spaces>else:`
at the same indent level. This deliberately captures the ENTIRE try/except
block (including any duplicate handlers that might be re-introduced),
so a non-greedy `.*?logger.error(...)` would stop too early and miss the
regression. The 02-02 plan regex `transfer_files_async.delay\(item\.hash\).*?
logger\.error\(.*?Failed to queue transfer` was a Rule 1 bug: with the
duplicate re-introduced, it would still count 1 because non-greedy stops
at the first handler. This test uses a boundary that catches both cases.
"""
import re
from pathlib import Path


def test_postprocessing_retry_has_single_except():
    """The PostProcessing retry branch must have exactly one
    `except Exception` handler; the duplicate at 1264-1265 is removed."""
    tasks_py = Path('itemqueue/tasks.py').read_text()
    # Match from `try:` through the body and all `except` handlers, bounded
    # by the next `\n<spaces>else:` at the same indent as the try block.
    # DOTALL makes `.` match newlines so the whole try/except body is one match.
    match = re.search(
        r"try:\s*\n\s+transfer_files_async\.delay\(item\.hash\).*?(?=\n\s+else:)",
        tasks_py,
        re.DOTALL,
    )
    assert match, "queue-retry try/except block not found"
    block = match.group(0)
    except_count = block.count('except Exception')
    assert except_count == 1, (
        f"Expected exactly 1 `except Exception` in queue-retry branch; "
        f"found {except_count}. Block:\n{block}"
    )
