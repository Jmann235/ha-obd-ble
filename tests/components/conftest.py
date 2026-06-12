"""Home Assistant harness tests — skipped unless the HA test plugin exists.

These run in CI (Linux) via requirements_test.txt; on a plain dev machine
without homeassistant installed, the whole directory skips cleanly.
"""

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let the HA test harness load custom_components/obd_ble."""
    return
