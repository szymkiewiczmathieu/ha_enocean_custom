"""The generic-profile catalog must never be read on the event loop."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:
    import homeassistant  # noqa: F401
    from homeassistant.core import HomeAssistant

    from custom_components.enocean_custom import eep_catalog

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare CI env
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant is not installed")
class CatalogWarmupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._dir.name)
        eep_catalog.built_in_profiles.cache_clear()

    async def asyncTearDown(self):
        eep_catalog.built_in_profiles.cache_clear()
        await self.hass.async_stop(force=True)
        self._dir.cleanup()

    async def test_catalog_is_built_off_the_event_loop(self):
        threads: list[int] = []
        real = eep_catalog._supplemental_profiles

        def spy():
            threads.append(threading.get_ident())
            return real()

        with mock.patch.object(eep_catalog, "_supplemental_profiles", spy):
            await eep_catalog.async_warm_built_in_profiles(self.hass)
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        # Warm cache: a later synchronous call does not read the file again.
        with mock.patch.object(
            eep_catalog, "_supplemental_profiles", side_effect=AssertionError
        ):
            self.assertIn("A5-12-02", eep_catalog.built_in_profiles())


if __name__ == "__main__":
    unittest.main()
