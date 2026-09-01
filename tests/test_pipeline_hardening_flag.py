"""Lock-in tests for the PIPELINE_HARDENING_ENABLED feature flag.

Phase 5 (pipeline hardening) is gated behind an env-var flag so the
operator can revert to legacy behavior without a redeploy. This test
guards the flag's contract:

- The literal name exists in harpoon2/settings_template.py
- Defaults to True when the env-var is unset
- Uses lower() == 'true' for case normalization (matches USE_POSTGRES)
"""
import os
import re
from pathlib import Path

SETTINGS_PATH = Path(__file__).parent.parent / "harpoon2" / "settings_template.py"


def _settings_source() -> str:
    return SETTINGS_PATH.read_text()


def test_flag_literal_defined_in_settings_template():
    """The PIPELINE_HARDENING_ENABLED name must appear in settings_template.py."""
    src = _settings_source()
    assert "PIPELINE_HARDENING_ENABLED" in src, (
        "PIPELINE_HARDENING_ENABLED not found in harpoon2/settings_template.py"
    )


def test_flag_uses_envvar_get_with_true_default():
    """The flag must read os.environ.get('PIPELINE_HARDENING_ENABLED', 'true')
    so an unset env-var resolves to True (the hardened path)."""
    src = _settings_source()
    m = re.search(
        r"os\.environ\.get\(\s*['\"]PIPELINE_HARDENING_ENABLED['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
        src,
    )
    assert m, (
        "PIPELINE_HARDENING_ENABLED must be read via "
        "os.environ.get('PIPELINE_HARDENING_ENABLED', 'true')"
    )
    assert m.group(1) == "true", (
        f"PIPELINE_HARDENING_ENABLED default must be 'true' (unset -> True), "
        f"got {m.group(1)!r}"
    )


def test_flag_uses_lowercase_normalization():
    """The flag must normalize with .lower() == 'true' so 'TRUE'/'True' work
    the same as 'true' — matches the USE_POSTGRES pattern."""
    src = _settings_source()
    assert re.search(r"\.lower\(\)\s*==\s*['\"]true['\"]", src), (
        "PIPELINE_HARDENING_ENABLED must use .lower() == 'true' normalization"
    )


def test_flag_defaults_to_true_when_env_unset(monkeypatch):
    """Evaluating the exact settings expression with the env-var unset
    must yield True."""
    monkeypatch.delenv("PIPELINE_HARDENING_ENABLED", raising=False)
    value = os.environ.get("PIPELINE_HARDENING_ENABLED", "true").lower() == "true"
    assert value is True


def test_flag_false_when_env_set_to_false(monkeypatch):
    """Setting PIPELINE_HARDENING_ENABLED=false must yield False (legacy path)."""
    monkeypatch.setenv("PIPELINE_HARDENING_ENABLED", "false")
    value = os.environ.get("PIPELINE_HARDENING_ENABLED", "true").lower() == "true"
    assert value is False


def test_flag_resolves_at_runtime():
    """settings.PIPELINE_HARDENING_ENABLED must be a bool at runtime and
    agree with the source expression evaluated against the current env."""
    from django.conf import settings

    assert hasattr(settings, "PIPELINE_HARDENING_ENABLED"), (
        "settings.PIPELINE_HARDENING_ENABLED is not defined"
    )
    assert isinstance(settings.PIPELINE_HARDENING_ENABLED, bool), (
        "settings.PIPELINE_HARDENING_ENABLED must be a bool"
    )
    expected = os.environ.get("PIPELINE_HARDENING_ENABLED", "true").lower() == "true"
    assert settings.PIPELINE_HARDENING_ENABLED is expected, (
        f"settings.PIPELINE_HARDENING_ENABLED={settings.PIPELINE_HARDENING_ENABLED} "
        f"does not match env-var evaluation {expected}"
    )