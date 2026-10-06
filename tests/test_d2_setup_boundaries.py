"""Boundary tests for D2-01-0A YAML and persisted options setup."""

from __future__ import annotations

import unittest
from typing import ClassVar
from unittest.mock import Mock, patch

import voluptuous as vol

from custom_components.enocean_custom.schema import valid_ui_devices
from custom_components.enocean_custom.switch import PLATFORM_SCHEMA, async_setup_entry


class D201BoundaryTests(unittest.TestCase):
    def _row(self, channel: int, eep: str = "D2-01-0A") -> dict:
        return {
            "id": [1, 2, 3, 4],
            "platform": "switch",
            "name": "ASP",
            "channel": channel,
            "switch_type": "default",
            "radio_metadata": {"eep": eep},
        }

    def test_yaml_schema_rejects_nonzero_asp_before_setup_side_effects(self):
        with self.assertRaises(vol.Invalid):
            PLATFORM_SCHEMA(
                {
                    "platform": "switch",
                    "id": [1, 2, 3, 4],
                    "name": "ASP",
                    "channel": 1,
                    "switch_type": "default",
                    "eep": "D2-01-0A",
                }
            )

    def test_yaml_schema_keeps_zero_and_other_eep_channels(self):
        zero = PLATFORM_SCHEMA(
            {
                "platform": "switch",
                "id": [1, 2, 3, 4],
                "name": "ASP",
                "channel": 0,
                "switch_type": "default",
                "eep": "D2-01-0A",
            }
        )
        other = PLATFORM_SCHEMA(
            {
                "platform": "switch",
                "id": [1, 2, 3, 4],
                "name": "Other",
                "channel": 31,
                "switch_type": "default",
                "eep": "D2-01-12",
            }
        )
        self.assertEqual(zero["channel"], 0)
        self.assertEqual(other["channel"], 31)

    def test_persisted_options_mixed_rows_keep_valid_and_drop_invalid(self):
        rows = valid_ui_devices([self._row(0), self._row(1), self._row(31, "D2-01-12")])
        self.assertEqual([row["channel"] for row in rows], [0, 31])

    def test_real_switch_constructor_rejects_invalid_asp_channel(self):
        from custom_components.enocean_custom.switch import EnOceanSwitch

        with self.assertRaisesRegex(ValueError, "channel 0 only"):
            EnOceanSwitch([1, 2, 3, 4], "Invalid ASP", 1, "default", "D2-01-0A")


class D201EntrySetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_setup_uses_metadata_eep_and_skips_invalid_row(self):
        class Entry:
            options: ClassVar[dict] = {
                "ui_devices": [
                    {
                        "id": [1, 2, 3, 4],
                        "platform": "switch",
                        "name": "ASP",
                        "channel": 0,
                        "switch_type": "default",
                        "radio_metadata": {"eep": "D2-01-0A"},
                    },
                    {
                        "id": [5, 6, 7, 8],
                        "platform": "switch",
                        "name": "Other",
                        "channel": 31,
                        "switch_type": "default",
                        "radio_metadata": {"eep": "D2-01-12"},
                    },
                    {
                        "id": [9, 10, 11, 12],
                        "platform": "switch",
                        "name": "Invalid ASP",
                        "channel": 1,
                        "switch_type": "default",
                        "radio_metadata": {"eep": "D2-01-0A"},
                    },
                ]
            }

        added = []
        with patch(
            "custom_components.enocean_custom.switch.EnOceanSwitch"
        ) as constructor:

            def make_entity(*args, **kwargs):
                entity = Mock()
                entity.set_radio_metadata.return_value = entity
                return entity

            constructor.side_effect = make_entity
            await async_setup_entry(object(), Entry(), added.extend)
        self.assertEqual([call[0][2] for call in constructor.call_args_list], [0, 31])
        self.assertEqual(
            [call[0][4] for call in constructor.call_args_list],
            ["D2-01-0A", "D2-01-12"],
        )
        self.assertEqual(len(added), 2)
