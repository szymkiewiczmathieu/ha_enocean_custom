"""pytest-homeassistant-custom-component wiring for the enocean_custom suite.

M9 (partial, deliberately): this directory is additive, not a replacement.
The unittest suite under ``tests/`` (stdlib, no HA dependency required to
collect) and ``tests/ha_smoke.py`` stay exactly as they are; this is the
"real HA, real config/options flow, real diagnostics snapshot" slice that a
bare-stdlib ``unittest.IsolatedAsyncioTestCase`` can only approximate.
"""

from __future__ import annotations

import pytest
from homeassistant.components import frontend

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def frontend_static_tree(tmp_path, monkeypatch):
    """Supply only the static package absent from the prepared HA test env.

    Dependency resolution, the real frontend setup and panel_custom still run;
    this fixture replaces no integration or panel behavior.
    """
    root = tmp_path / "hass_frontend"
    root.mkdir()
    for directory in ("static", "frontend_latest", "frontend_es5"):
        (root / directory).mkdir()
    for filename in (
        "service_worker.js",
        "sw-modern.js",
        "sw-modern.js.map",
        "sw-legacy.js",
        "sw-legacy.js.map",
        "robots.txt",
        "onboarding.html",
        "authorize.html",
    ):
        (root / filename).touch()
    monkeypatch.setattr(frontend, "_frontend_root", lambda _repo_path: root)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/enocean_custom for real."""
    return enable_custom_integrations
