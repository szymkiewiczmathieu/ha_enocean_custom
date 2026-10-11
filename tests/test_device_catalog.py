import unittest

from custom_components.enocean_custom.device_catalog import (
    UBIWIZZ_LIGHT_D2_01_08,
    get_commissioning_profile,
)


class DeviceCatalogTests(unittest.TestCase):
    def test_field_verified_ubiwizz_recipe_is_explicit(self):
        profile = get_commissioning_profile("ubiwizz_light_d2_01_08")
        self.assertEqual(profile, UBIWIZZ_LIGHT_D2_01_08)
        self.assertEqual(profile.eep, "D2-01-08")
        self.assertEqual(profile.channels, (0, 1))
        self.assertEqual(profile.manufacturer_id, 0x046)
        self.assertEqual(profile.press_count, 3)

    def test_unknown_product_is_not_inferred(self):
        self.assertIsNone(get_commissioning_profile("D2-01-08"))
        self.assertIsNone(get_commissioning_profile("04B382DA"))


if __name__ == "__main__":
    unittest.main()
