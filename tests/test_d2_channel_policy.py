"""D2-01-0A product policy tests."""

from unittest import TestCase

from custom_components.enocean_custom.switch import EnOceanSwitch


class D201ChannelPolicyTests(TestCase):
    def test_nodon_asp_is_single_channel_zero(self) -> None:
        entity = EnOceanSwitch([1, 2, 3, 4], "ASP", 0, "default", "D2-01-0A")
        self.assertEqual(entity.channel, 0)

    def test_nodon_asp_rejects_nonzero_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "channel 0 only"):
            EnOceanSwitch([1, 2, 3, 4], "ASP", 1, "default", "D2-01-0A")

    def test_other_eep_keeps_generic_channel_range(self) -> None:
        entity = EnOceanSwitch([1, 2, 3, 4], "generic", 31, "default", "D2-01-12")
        self.assertEqual(entity.channel, 31)
