"""Tenacity retry decorator factories for network-bound call sites.

Phase 5 (PIPE-07): wrap transient ConnectionError / Timeout / SSHException
failures with exponential backoff so a transient network blip doesn't burn a
Celery worker slot for the full task_time_limit and doesn't trigger manual
operator intervention.

The four tenacity knobs (per 05-RESEARCH.md §"Pattern 2"):
  - stop=stop_after_attempt(5)                       — bounded retry budget
  - wait=wait_random_exponential(multiplier=1, max=60) — jittered backoff, 60s cap
  - retry=retry_if_exception_type(...)               — only transient network exceptions
  - reraise=True                                     — caller sees the original
                                                       exception, not tenacity's RetryError

dplibs is an implicit namespace package (no __init__.py) — same convention as
dplibs/session.py, dplibs/search.py, and dplibs/filesystem.py.
"""
import io
import requests
import paramiko
import paramiko.ssh_exception

from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)

# Exceptions that warrant retrying a Manager API call. HTTP-level errors
# (4xx/5xx) are NOT retried — they propagate immediately to the caller.
_API_RETRY_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

# Exceptions that warrant retrying an SFTP connect handshake.
_SFTP_RETRY_EXC = (
    paramiko.ssh_exception.SSHException,
    paramiko.ssh_exception.NoValidConnectionsError,
    ConnectionError,   # socket-level
    TimeoutError,
)


def api_retry():
    """Return a tenacity @retry decorator for Manager API calls.

    Retries requests.exceptions.ConnectionError and requests.exceptions.Timeout
    with exponential backoff (jittered, capped at 60s per attempt), 5 attempts
    max. reraise=True means the caller sees the original exception after
    exhaustion, not tenacity's RetryError.
    """
    return retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_exception_type(_API_RETRY_EXC),
        reraise=True,
    )


def sftp_retry():
    """Return a tenacity @retry decorator for SFTP connect handshakes.

    Retries paramiko SSHException / NoValidConnectionsError plus socket-level
    ConnectionError / TimeoutError with exponential backoff (jittered, capped
    at 60s per attempt), 5 attempts max. reraise=True preserves the original
    exception for the caller.
    """
    return retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_exception_type(_SFTP_RETRY_EXC),
        reraise=True,
    )


@sftp_retry()
def _sftp_connect_with_retry(seedbox):
    """Connect to the seedbox via paramiko, retrying transient failures.

    Distinct from itemqueue.tasks._sftp_get_with_timeout — that is a wall-clock
    ceiling on a single sftp.get(); this is exponential backoff on the connect
    handshake. Returns the connected paramiko.SSHClient.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if seedbox.auth_type == 'password':
        ssh.connect(
            seedbox.host, port=seedbox.port, username=seedbox.username,
            password=seedbox.password, timeout=10,
        )
    else:
        pkey = _load_rsa_key(seedbox.ssh_key)
        ssh.connect(
            seedbox.host, port=seedbox.port, username=seedbox.username,
            pkey=pkey, timeout=10,
        )
    return ssh


def _load_rsa_key(key_string):
    """Load an RSA private key from a string, across paramiko versions.

    paramiko 5.0 removed RSAKey.from_private_key_string; the file-like
    from_private_key API works on every supported version (>= 3.4). Try the
    legacy API first (matches the pre-existing call sites), fall back to the
    modern one on AttributeError.
    """
    try:
        return paramiko.RSAKey.from_private_key_string(key_string)
    except AttributeError:
        return paramiko.RSAKey.from_private_key(io.StringIO(key_string))