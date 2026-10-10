"""Safety boundaries for Ubiwizz repeater diagnostics."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace

_ROOT = Path(__file__).parents[1]
_COMPONENT = _ROOT / "custom_components" / "enocean_custom"

# Keep the policy contract independently testable in the dependency-free suite.
_PKG = "_ubiwizz_repeater"
sys.modules.setdefault(_PKG, types.ModuleType(_PKG))
_SPEC = importlib.util.spec_from_file_location(
    f"{_PKG}.policy", _COMPONENT / "ubiwizz_repeater.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_POLICY = importlib.util.module_from_spec(_SPEC)
sys.modules[f"{_PKG}.policy"] = _POLICY
_SPEC.loader.exec_module(_POLICY)

parse_ubiwizz_repeater_request = _POLICY.parse_ubiwizz_repeater_request
ubiwizz_repeater_diagnostics = _POLICY.ubiwizz_repeater_diagnostics


class UbiwizzRepeaterPolicyTests(unittest.TestCase):
    """Only bounded candidates and levels reach the no-radio UI boundary."""

    def test_accepts_only_exact_candidate_profiles_and_levels(self) -> None:
        self.assertEqual(
            parse_ubiwizz_repeater_request("D2-01-01", "1"),
            _POLICY.UbiwizzRepeaterDiagnosticRequest("D2-01-01", 1),
        )
        self.assertEqual(
            parse_ubiwizz_repeater_request("D2-01-12", 2),
            _POLICY.UbiwizzRepeaterDiagnosticRequest("D2-01-12", 2),
        )
        for profile, level in (
            ("d2-01-12", "1"),
            (" D2-01-12", "1"),
            ("D2-01-0A", "1"),
            ("D2-01-12", 0),
            ("D2-01-12", 3),
            ("D2-01-12", 1.0),
            ("D2-01-12", True),
            ("D2-01-12", " 1"),
        ):
            with self.subTest(profile=profile, level=level):
                self.assertIsNone(parse_ubiwizz_repeater_request(profile, level))

    def test_diagnostics_never_claim_a_live_state_or_radio_command(self) -> None:
        self.assertEqual(
            ubiwizz_repeater_diagnostics(),
            {
                "candidate_eep_profiles": ["D2-01-01", "D2-01-12"],
                "documented_default": "not_documented",
                "requested_levels": [1, 2],
                "runtime_state": "unknown",
                "radio_command": "not_implemented",
            },
        )


try:
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.enocean_custom.options_flow import EnOceanOptionsFlow
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by CI without HA
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class UbiwizzRepeaterOptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    """The options UI is diagnostic-only and never reaches the radio transport."""

    async def asyncSetUp(self) -> None:
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})

    async def asyncTearDown(self) -> None:
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry_and_flow(self):
        entry = ConfigEntry(
            data={"device": "/dev/test-ubiwizz-repeater"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={CONF_UI_DEVICES: []},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        entry.runtime_data = SimpleNamespace(sent=[])
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = EnOceanOptionsFlow()
        flow.hass = self.hass
        flow.handler = entry.entry_id
        return entry, flow

    async def test_diagnostic_selection_never_sends_or_persists(self) -> None:
        entry, flow = self._entry_and_flow()
        before = dict(entry.options)

        shown = await flow.async_step_ubiwizz_repeater()
        self.assertEqual(shown["step_id"], "ubiwizz_repeater")
        result = await flow.async_step_ubiwizz_repeater(
            {"ubiwizz_repeater_eep": "D2-01-12", "ubiwizz_repeater_level": "2"}
        )

        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["step_id"], "ubiwizz_repeater_blocked")
        self.assertEqual(
            result["description_placeholders"],
            {"eep": "D2-01-12", "level": "2"},
        )
        self.assertEqual(entry.runtime_data.sent, [])
        self.assertEqual(entry.options, before)

        closed = await flow.async_step_ubiwizz_repeater_blocked({})
        self.assertEqual(closed["type"], FlowResultType.CREATE_ENTRY)
        self.assertEqual(closed["data"], before)
        self.assertEqual(entry.runtime_data.sent, [])
        self.assertEqual(entry.options, before)

    async def test_forged_profile_or_level_is_rejected_before_any_side_effect(
        self,
    ) -> None:
        entry, flow = self._entry_and_flow()
        before = dict(entry.options)

        for profile, level in (("D2-01-0A", "1"), ("D2-01-12", "0")):
            with self.subTest(profile=profile, level=level):
                result = await flow.async_step_ubiwizz_repeater(
                    {"ubiwizz_repeater_eep": profile, "ubiwizz_repeater_level": level}
                )
                self.assertEqual(result["errors"], {"base": "invalid_ubiwizz_repeater"})
                self.assertEqual(entry.runtime_data.sent, [])
                self.assertEqual(entry.options, before)

    async def test_exported_diagnostics_are_static_and_identifier_free(self) -> None:
        entry = SimpleNamespace(
            data={"device": "/dev/private", "id": [1, 2, 3, 4]},
            options={CONF_UI_DEVICES: [{"id": [5, 6, 7, 8], "name": "Private"}]},
            runtime_data=SimpleNamespace(diagnostics=lambda: {"available": True}),
        )

        exported = await async_get_config_entry_diagnostics(None, entry)

        self.assertEqual(exported["ubiwizz_repeater"], ubiwizz_repeater_diagnostics())
        self.assertNotIn("/dev/private", repr(exported))
        self.assertNotIn("[5, 6, 7, 8]", repr(exported))


if __name__ == "__main__":
    unittest.main()
