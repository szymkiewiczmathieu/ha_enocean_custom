"""Actionable, translated errors for refused commands and teach-ins.

Quality Scale ``action-exceptions`` / ``exception-translations``: every error a
user can trigger through an action carries ``translation_domain`` and a key
that exists, with the same placeholders, in ``strings.json`` and ``fr.json``.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

    from custom_components.enocean_custom import climate, light, switch
    from custom_components.enocean_custom.const import DOMAIN

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

COMPONENT = ROOT / "custom_components/enocean_custom"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def _hass_with_gateway(gateway) -> SimpleNamespace:
    entry = SimpleNamespace(runtime_data=gateway)
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry]),
        data={},
    )


class RefusingDongle:
    """A dongle that knows its Base ID but refuses to queue anything."""

    base_id = (0xA1, 0xB2, 0xC3, 0xD4)
    available = False

    def send(self, *_args, **_kwargs) -> bool:
        return False


class ExceptionCatalogTests(unittest.TestCase):
    """The exception catalog is complete and consistent across languages."""

    def _messages(self, name: str) -> dict[str, str]:
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        return {key: value["message"] for key, value in data["exceptions"].items()}

    def test_every_language_shares_keys_and_placeholders(self):
        english = self._messages("strings.json")
        self.assertEqual(english, self._messages("translations/en.json"))
        french = self._messages("translations/fr.json")
        self.assertEqual(set(english), set(french))
        for key, message in english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    set(_PLACEHOLDER.findall(message)),
                    set(_PLACEHOLDER.findall(french[key])),
                )
                self.assertNotEqual(message, french[key])

    def test_expected_keys_exist(self):
        self.assertEqual(
            set(self._messages("strings.json")),
            {
                "base_id_unavailable",
                "command_rejected",
                "light_sender_missing",
                "teach_in_rejected",
                "rocker_teach_in_not_supported",
                "learn_in_progress",
                "gateway_not_loaded",
            },
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RaisedErrorTests(unittest.TestCase):
    """Each refusal raises the documented key with its declared placeholders."""

    def setUp(self):
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        self.messages = {
            key: value["message"] for key, value in strings["exceptions"].items()
        }

    def _assert_translated(self, raised, key: str, exception_type=HomeAssistantError):
        error = raised.exception
        self.assertIsInstance(error, exception_type)
        self.assertEqual(error.translation_domain, DOMAIN)
        self.assertEqual(error.translation_key, key)
        self.assertEqual(
            set(_PLACEHOLDER.findall(self.messages[key])),
            set(error.translation_placeholders or {}),
        )

    def test_d2_switch_without_base_id(self):
        entity = switch.EnOceanSwitch([1, 2, 3, 4], "Relay", 0, "default")
        entity.hass = _hass_with_gateway(SimpleNamespace(base_id=None))
        with self.assertRaises(HomeAssistantError) as raised:
            entity.turn_on()
        self._assert_translated(raised, "base_id_unavailable")
        self.assertEqual(raised.exception.translation_placeholders["name"], "Relay")

    def test_switch_command_refused_by_the_dongle(self):
        for switch_type in ("default", "RPS"):
            with self.subTest(switch_type=switch_type):
                entity = switch.EnOceanSwitch([1, 2, 3, 4], "Relay", 0, switch_type)
                entity.hass = _hass_with_gateway(RefusingDongle())
                entity.schedule_update_ha_state = lambda: None
                with self.assertRaises(HomeAssistantError) as raised:
                    entity.turn_off()
                self._assert_translated(raised, "command_rejected")
                # The transport result is still recorded for the attributes.
                self.assertIs(
                    entity.extra_state_attributes["last_command_esp3_accepted"],
                    False,
                )
                self.assertIsNone(entity.is_on)

    def test_light_without_sender_identity(self):
        entity = light.EnOceanLight(None, [1, 2, 3, 4], "Dimmer")
        with self.assertRaises(HomeAssistantError) as raised:
            entity.turn_on(brightness=128)
        self._assert_translated(raised, "light_sender_missing")
        with self.assertRaises(ServiceValidationError) as raised:
            entity.send_teach_in()
        self._assert_translated(raised, "light_sender_missing", ServiceValidationError)

    def test_light_command_and_teach_in_refused_by_the_dongle(self):
        entity = light.EnOceanLight([5, 6, 7, 8], [1, 2, 3, 4], "Dimmer")
        entity.hass = _hass_with_gateway(RefusingDongle())
        entity.schedule_update_ha_state = lambda: None
        with self.assertRaises(HomeAssistantError) as raised:
            entity.turn_on()
        self._assert_translated(raised, "command_rejected")
        self.assertIsNone(entity.is_on)
        with self.assertRaises(HomeAssistantError) as raised:
            entity.send_teach_in()
        self._assert_translated(raised, "teach_in_rejected")

    def _climate(self, profile: str):
        entity = climate.EnOceanClimate(
            [1, 2, 3, 4],
            "Valve",
            [5, 6, 7, 8],
            "sensor.room",
            8.0,
            5,
            0.5,
            21.0,
            4.0,
            timedelta(minutes=17),
            5.0,
            240.0,
            device_type=profile,
        )
        entity.hass = _hass_with_gateway(RefusingDongle())
        entity.schedule_update_ha_state = lambda: None
        return entity

    def test_climate_teach_in_refused_by_the_dongle(self):
        for profile in (climate.PROFILE_SRC_D08, climate.PROFILE_A5_20_04):
            with self.subTest(profile=profile):
                entity = self._climate(profile)
                with self.assertRaises(HomeAssistantError) as raised:
                    entity.teach_in_actor()
                self._assert_translated(raised, "teach_in_rejected")

    def test_rocker_teach_in_on_a_valve_profile_is_a_validation_error(self):
        entity = self._climate(climate.PROFILE_A5_20_04)
        with self.assertRaises(ServiceValidationError) as raised:
            entity.teach_in_actor_switch()
        self._assert_translated(
            raised, "rocker_teach_in_not_supported", ServiceValidationError
        )
        self.assertEqual(
            raised.exception.translation_placeholders["profile"],
            climate.PROFILE_A5_20_04,
        )
        with self.assertRaises(HomeAssistantError) as raised:
            self._climate(climate.PROFILE_SRC_D08).teach_in_actor_switch()
        self._assert_translated(raised, "teach_in_rejected")


if __name__ == "__main__":
    unittest.main()
