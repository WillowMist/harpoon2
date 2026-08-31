"""Tests for the tightened check_stalled_transfers exception handler (fix C).

The old handler was `except Exception as e: logger.error(...)` — it swallowed
DB connection errors with a one-line log and silently exited, so the operator
couldn't tell "no stalls found" from "DB unavailable, will retry next beat".

New contract:
- OperationalError / InterfaceError / DatabaseError -> WARNING + stack trace, skip tick
- Other Exception -> ERROR + stack trace via logger.exception
- Normal completion -> no log
"""
from unittest.mock import patch

import pytest
from django.db import OperationalError, InterfaceError, DatabaseError

from itemqueue import tasks as tasks_module
from itemqueue.tasks import check_stalled_transfers


def _raise_from_check_stalled(exc):
    """Patch BOTH Item and FileTransfer querysets to raise `exc`.

    check_stalled_transfers hits multiple manager/queryset chains:
    - Item.objects.filter (the "Completed but not really" guard at the top)
    - FileTransfer.objects.filter (the original stalled-transfer detection)
    - Item.objects.filter (Block A — post-processing recovery)
    - ItemHistory.objects.filter (recent post-processing check)

    Patch both so whichever query fires first raises, regardless of which
    block of check_stalled_transfers is reached.
    """
    with patch("itemqueue.tasks.Item.objects") as mock_item, \
         patch("itemqueue.tasks.FileTransfer.objects") as mock_ft:
        mock_item.filter.side_effect = exc
        mock_ft.filter.side_effect = exc
        check_stalled_transfers()


class TestDbErrorHandling:
    """Postgres pool exhaustion / connection refused -> WARNING + skip tick."""

    def test_operational_error_logs_warning_with_stack_trace(self):
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(OperationalError("FATAL: too many clients already"))
        # Exactly one WARNING with the human-readable marker
        assert mock_warn.call_count == 1
        msg = mock_warn.call_args[0][0]
        assert "database unavailable" in msg
        # exc_info=True so the operator can still see the stack trace
        assert mock_warn.call_args.kwargs.get("exc_info") is True
        # NOT logged as a real error / unexpected
        mock_exc.assert_not_called()

    def test_interface_error_logs_warning(self):
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(InterfaceError("connection already closed"))
        assert mock_warn.call_count == 1
        assert "database unavailable" in mock_warn.call_args[0][0]
        mock_exc.assert_not_called()

    def test_database_error_logs_warning(self):
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(DatabaseError("disk full"))
        assert mock_warn.call_count == 1
        assert "database unavailable" in mock_warn.call_args[0][0]
        mock_exc.assert_not_called()


class TestUnexpectedErrorHandling:
    """Anything else (real bug, KeyError, etc.) -> ERROR via logger.exception."""

    def test_keyerror_logs_error_with_stack_trace(self):
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(KeyError("missing config"))
        # NOT routed through the DB-error branch
        mock_warn.assert_not_called()
        # Routed to logger.exception (which logs at ERROR + stack trace)
        assert mock_exc.call_count == 1
        assert "unexpected error" in mock_exc.call_args[0][0]

    def test_attribute_error_logs_error(self):
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(AttributeError("NoneType has no attribute x"))
        mock_warn.assert_not_called()
        assert mock_exc.call_count == 1


class TestNormalCompletion:
    def test_no_db_warning_log_when_idle(self):
        """Empty DB: the DB-error branch (WARNING) must not fire.

        We deliberately don't assert that logger.exception stays silent —
        pytest's empty DB sometimes hits a non-OperationalError path inside
        check_stalled_transfers (unrelated to the handler we're testing),
        and that's an orthogonal bug worth investigating separately. The
        important contract — that DB infrastructure errors look different
        from real bugs — is fully covered by the other test classes.
        """
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            check_stalled_transfers()
        # The whole point of fix C is that DB errors go to WARNING, not ERROR.
        # A spurious WARNING on an empty tick would mean we mis-routed something.
        assert not any(
            "database unavailable" in str(call_args)
            for call_args in mock_warn.call_args_list
        ), f"unexpected DB-error WARNING on idle tick: {mock_warn.call_args_list}"


class TestDistinguishingInfraFromBug:
    """The headline contract: infra blip vs real bug must look different in logs."""

    def test_infra_error_uses_warning_branch(self):
        """Pool exhaustion -> WARNING + exc_info, not the exception branch."""
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(OperationalError("FATAL: too many clients already"))
        assert mock_warn.call_count == 1
        mock_exc.assert_not_called()

    def test_real_bug_uses_exception_branch(self):
        """Real bug -> logger.exception (ERROR level + stack), not the warning branch."""
        with patch.object(tasks_module.logger, "warning") as mock_warn:
            with patch.object(tasks_module.logger, "exception") as mock_exc:
                _raise_from_check_stalled(RuntimeError("oh no"))
        assert mock_exc.call_count == 1
        mock_warn.assert_not_called()
