"""End-to-end persisted UI option policy for the NodOn ASP actuator."""

from unittest import TestCase

from custom_components.enocean_custom.schema import valid_ui_devices


class D201UiOptionPolicyTests(TestCase):
    def _row(self, channel: int, eep: str = "D2-01-0A") -> dict:
        return {
            "id": [1, 2, 3, 4],
            "platform": "switch",
            "name": "ASP",
            "channel": channel,
            "switch_type": "default",
            "radio_metadata": {"eep": eep},
        }

    def test_options_channel_zero_is_kept(self) -> None:
        rows = valid_ui_devices([self._row(0)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["radio_metadata"]["eep"], "D2-01-0A")

    def test_options_nonzero_asp_channel_is_discarded(self) -> None:
        self.assertEqual(valid_ui_devices([self._row(1)]), [])

    def test_options_other_d2_eep_keeps_channel(self) -> None:
        rows = valid_ui_devices([self._row(31, "D2-01-12")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["channel"], 31)
