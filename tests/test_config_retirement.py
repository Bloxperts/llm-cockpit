"""Retirement defaults for archived Cockpit."""

from cockpit.config import Settings


def test_conductor_observer_is_disabled_by_default() -> None:
    settings = Settings()

    assert settings.conductor_enabled is False
    assert settings.conductor_ssh_host == ""
