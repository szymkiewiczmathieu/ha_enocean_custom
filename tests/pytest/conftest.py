"""pytest-homeassistant-custom-component wiring for the enocean_custom suite.

M9 (partial, deliberately): this directory is additive, not a replacement.
The unittest suite under ``tests/`` (stdlib, no HA dependency required to
collect) and ``tests/ha_smoke.py`` stay exactly as they are; this is the
"real HA, real config/options flow, real diagnostics snapshot" slice that a
bare-stdlib ``unittest.IsolatedAsyncioTestCase`` can only approximate.
"""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/enocean_custom for real."""
    return enable_custom_integrations
