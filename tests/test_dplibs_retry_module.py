"""Lock-in test for dplibs/retry.py (PIPE-07).

Pins the module shape: the three exports, the tenacity-knob literals in
source, and the callable signature of _sftp_connect_with_retry. A regression
that renames an export or changes the retry budget (stop/wait/reraise) is
caught at CI time.
"""
import inspect

import dplibs.retry as retry_module
from dplibs.retry import (
    api_retry,
    sftp_retry,
    _sftp_connect_with_retry,
    _API_RETRY_EXC,
    _SFTP_RETRY_EXC,
)


def test_module_exports_are_callable():
    assert callable(api_retry)
    assert callable(sftp_retry)
    assert callable(_sftp_connect_with_retry)


def test_factories_return_working_decorators():
    """Applying a factory to a function yields a tenacity-wrapped callable
    that still invokes the original function."""
    wrapped = api_retry()(lambda: 42)
    assert callable(wrapped)
    assert wrapped() == 42
    # tenacity's wrapped callables expose `retry` and `statistics`.
    assert hasattr(wrapped, 'retry')
    assert hasattr(wrapped, 'statistics')


def test_sftp_connect_accepts_one_positional_arg():
    sig = inspect.signature(_sftp_connect_with_retry)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == 'seedbox'


def test_exception_tuples_are_well_formed():
    import requests
    import paramiko.ssh_exception

    assert requests.exceptions.ConnectionError in _API_RETRY_EXC
    assert requests.exceptions.Timeout in _API_RETRY_EXC
    assert paramiko.ssh_exception.SSHException in _SFTP_RETRY_EXC
    assert paramiko.ssh_exception.NoValidConnectionsError in _SFTP_RETRY_EXC
    assert ConnectionError in _SFTP_RETRY_EXC
    assert TimeoutError in _SFTP_RETRY_EXC


def test_source_pins_tenacity_knobs():
    """The three tenacity knobs must be present in the module source:
    stop_after_attempt(5), wait_random_exponential(multiplier=1, max=60),
    and reraise=True."""
    src = inspect.getsource(retry_module)
    assert 'stop_after_attempt(5)' in src, (
        "retry budget must be stop_after_attempt(5)"
    )
    assert 'wait_random_exponential(multiplier=1, max=60)' in src, (
        "backoff must be wait_random_exponential(multiplier=1, max=60)"
    )
    assert 'reraise=True' in src, (
        "reraise=True is required so the caller sees the original exception"
    )