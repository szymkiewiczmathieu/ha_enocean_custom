"""Source-backed Ubiwizz installation-profile and EEP regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

import voluptuous as vol

from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA
from custom_components.enocean_custom.sensor import (
    SENSOR_DESC_CONTACT,
    SENSOR_DESC_WINDOWHANDLE,
    EnOceanD50001Contact,
    EnOceanWindowHandle,
)
from custom_components.enocean_custom.switch import EnOceanSwitch, PLATFORM_SCHEMA
from custom_components.enocean_custom.ubiwizz import (
    UBID1507C,
    UBIWIZZ_UBID1507C_MANUAL_URL,
    UBIWIZZ_UBID1507C_PRODUCT_URL,
)


class UbiwizzInstallationProfileTests(TestCase):
    """Keep UBID1507C facts explicit and bounded to operator selection."""

    def test_ubid1507c_profile_matches_the_official_product_material(self) -> None:
        self.assertEqual(UBID1507C.model, "UBID1507C")
        self.assertEqual(UBID1507C.eep, "D2-01-12")
        self.assertEqual(UBID1507C.output_channels, (0, 1))
        self.assertEqual(UBID1507C.rated_output_current_a, 5)
        self.assertEqual(UBID1507C.manual_url, UBIWIZZ_UBID1507C_MANUAL_URL)
        self.assertEqual(UBID1507C.product_url, UBIWIZZ_UBID1507C_PRODUCT_URL)

    def test_explicit_profile_accepts_two_channels_and_rejects_other_values(self) -> None:
        for channel in UBID1507C.output_channels:
            config = PLATFORM_SCHEMA(
                {
                    "platform": "switch",
                    "id": [1, 2, 3, 4],
                    "name": f"Ubiwizz {channel}",
                    "channel": channel,
                    "switch_type": "default",
                    "eep": "D2-01-12",
                    "actuator_profile": UBID1507C.profile_id,
                }
            )
            self.assertEqual(config["channel"], channel)

        with self.assertRaisesRegex(vol.Invalid, "channels 0 and 1 only"):
            PLATFORM_SCHEMA(
                {
                    "platform": "switch",
                    "id": [1, 2, 3, 4],
                    "name": "invalid Ubiwizz channel",
                    "channel": 2,
                    "switch_type": "default",
                    "actuator_profile": UBID1507C.profile_id,
                }
            )

    def test_persisted_profile_validates_channel_without_reclassifying_generic_d2(self) -> None:
        base = {
            "id": [1, 2, 3, 4],
            "platform": "switch",
            "name": "Ubiwizz",
            "channel": 1,
            "switch_type": "default",
            "actuator_profile": UBID1507C.profile_id,
            "radio_metadata": {"eep": "D2-01-12"},
        }
        self.assertEqual(UI_DEVICE_SCHEMA(base)["channel"], 1)

        with self.assertRaisesRegex(vol.Invalid, "channels 0 and 1 only"):
            UI_DEVICE_SCHEMA({**base, "channel": 2})
        with self.assertRaisesRegex(vol.Invalid, "requires the D2-01-12 EEP"):
            UI_DEVICE_SCHEMA(
                {**base, "radio_metadata": {"eep": "A5-12-01"}}
            )

        generic = EnOceanSwitch(
            [1, 2, 3, 4], "generic D2", 31, "default", "D2-01-12"
        )
        self.assertEqual(generic.channel, 31)

    def test_channels_update_only_from_their_own_d2_status_feedback(self) -> None:
        sender = [1, 2, 3, 4]
        channel_0 = EnOceanSwitch(
            sender, "Ubiwizz output 1", 0, "default", "D2-01-12", UBID1507C.profile_id
        )
        channel_1 = EnOceanSwitch(
            sender, "Ubiwizz output 2", 1, "default", "D2-01-12", UBID1507C.profile_id
        )
        for entity in (channel_0, channel_1):
            entity.async_write_ha_state = lambda: None  # type: ignore[method-assign]

        acknowledgements = []
        channel_0.send_command = (  # type: ignore[method-assign]
            lambda _data, _optional, _packet_type, response_callback=None: (
                acknowledgements.append(response_callback) or True
            )
        )
        channel_0._send_state_packets([([RORG.VLD, 0x01, 0x00, 100], [])])
        acknowledgements[0](True)
        self.assertIsNone(channel_0.is_on)

        channel_0.value_changed(
            SimpleNamespace(data=[RORG.VLD, 0x04, 0x00, 100, *sender, 0x00])
        )
        self.assertIs(channel_0.is_on, True)
        self.assertIsNone(channel_1.is_on)
        self.assertEqual(channel_0.extra_state_attributes["d2_output_value"], 100)
        self.assertEqual(
            channel_0.extra_state_attributes["configured_actuator_profile"],
            UBID1507C.profile_id,
        )

        channel_1.value_changed(
            SimpleNamespace(data=[RORG.VLD, 0x04, 0x01, 0x00, *sender, 0x00])
        )
        self.assertIs(channel_0.is_on, True)
        self.assertIs(channel_1.is_on, False)
        self.assertEqual(channel_1.extra_state_attributes["d2_channel"], 1)


class HoppeAndContactEepTests(TestCase):
    """Decode documented F6-10-00 and D5-00-01 payloads without guessing."""

    @staticmethod
    def _sensor(entity):
        entity.schedule_update_ha_state = lambda: None  # type: ignore[method-assign]
        return entity

    def test_f6_10_00_uses_high_nibble_and_reports_unknown(self) -> None:
        entity = self._sensor(
            EnOceanWindowHandle([1, 2, 3, 4], "handle", SENSOR_DESC_WINDOWHANDLE)
        )
        for payload, expected in (
            (0xC1, "open"),
            (0xE7, "open"),
            (0xD0, "tilt"),
            (0xF3, "closed"),
            (0xB0, "unknown"),
        ):
            entity.value_changed(SimpleNamespace(rorg=RORG.RPS, data=[RORG.RPS, payload]))
            self.assertEqual(entity.native_value, expected)

        entity.value_changed(SimpleNamespace(rorg=RORG.RPS, data=[RORG.RPS]))
        self.assertEqual(entity.native_value, "unknown")

    def test_d5_00_01_maps_data_and_ignores_teach_in(self) -> None:
        entity = self._sensor(
            EnOceanD50001Contact([1, 2, 3, 4], "contact", SENSOR_DESC_CONTACT)
        )
        entity.value_changed(SimpleNamespace(rorg=RORG.BS1, data=[RORG.BS1, 0x08]))
        self.assertEqual(entity.native_value, "open")
        entity.value_changed(SimpleNamespace(rorg=RORG.BS1, data=[RORG.BS1, 0x09]))
        self.assertEqual(entity.native_value, "closed")
        for teach_in in (0x00, 0x01):
            entity.value_changed(
                SimpleNamespace(rorg=RORG.BS1, data=[RORG.BS1, teach_in])
            )
            self.assertEqual(entity.native_value, "closed")
