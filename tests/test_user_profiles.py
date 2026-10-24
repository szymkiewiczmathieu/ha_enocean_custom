"""Operator-defined generic profiles (A6): plain JSON, strict schema, no override.

``<config>/enocean_custom/profiles.json`` may add EEPs the vendored
``EEP.xml`` lacks. It is data only (``json.loads`` plus a strict voluptuous
schema); a profile a built-in decoder already covers is refused, never
shadowed; an invalid file disables every user profile and raises a Repair
issue; the accepted profiles decode through the very same generic path as
the XML ones and mint the same identity formulas.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    # Imported first: Home Assistant 2026.10 aliases ``voluptuous`` to probatio.
    import homeassistant  # noqa: F401
    import voluptuous as vol
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import issue_registry as ir

    from custom_components.enocean_custom import eep_catalog, user_profiles
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
    from custom_components.enocean_custom.identity import generic_eep_identities
    from custom_components.enocean_custom.schema import UI_DEVICE_SCHEMA

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [0x05, 0x8F, 0x22, 0x33]

VALID_PROFILE = {
    "eep": "A5-02-7A",
    "description": "Synthetic 10-bit temperature, -40..62.5 (unassigned TYPE)",
    "fields": [
        {
            "shortcut": "TMP",
            "description": "Temperature",
            "kind": "value",
            "offset": 14,
            "size": 10,
            "unit": "C",
            "range": {"min": 1023, "max": 0},
            "scale": {"min": -40, "max": 62.5},
        },
        {
            "shortcut": "PIR",
            "kind": "enum",
            "offset": 24,
            "size": 1,
        },
    ],
}


def _document(*profiles):
    return json.dumps({"version": 1, "profiles": list(profiles)})


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ParseTests(unittest.TestCase):
    def tearDown(self):
        eep_catalog.set_user_profiles({})

    def test_a_valid_file_builds_a_decodable_profile(self):
        accepted, refused = user_profiles.parse_user_profiles(_document(VALID_PROFILE))
        self.assertEqual(refused, {})
        profile = accepted["A5-02-7A"]
        self.assertEqual((profile.rorg, profile.func, profile.type), (0xA5, 0x02, 0x7A))
        self.assertEqual([f.slug for f in profile.exposed_fields], ["tmp", "pir"])
        # 10-bit raw 0 -> 62.5, raw 1023 -> -40 (inverted range like A5-02-xx);
        # DB0 bit 3 (LRN) set marks a data telegram.
        for raw, expected in ((0, 62.5), (1023, -40.0), (512, 11.2)):
            bits = (raw << (32 - 14 - 10)) | 0x08
            payload = bits.to_bytes(4, "big")
            decoded = profile.decode([RORG.BS4, *payload, *SENDER, 0])
            self.assertEqual(decoded["tmp"], expected)
            self.assertEqual(decoded["pir"], 0)
        # A teach-in telegram (LRN bit clear) decodes to nothing.
        self.assertIsNone(profile.decode([RORG.BS4, 0, 0, 0, 0, *SENDER, 0]))

    def test_built_in_and_reserved_profiles_are_refused_not_overridden(self):
        built_in = next(iter(eep_catalog.built_in_profiles()))
        accepted, refused = user_profiles.parse_user_profiles(
            _document(
                {**VALID_PROFILE, "eep": built_in},
                {**VALID_PROFILE, "eep": "D2-01-0A"},
                {**VALID_PROFILE, "eep": "A5-20-01"},
                {**VALID_PROFILE, "eep": "F6-02-01"},
                VALID_PROFILE,
                VALID_PROFILE,
            )
        )
        self.assertEqual(list(accepted), ["A5-02-7A"])
        self.assertEqual(
            set(refused), {built_in, "D2-01-0A", "A5-20-01", "F6-02-01"} | {"A5-02-7A"}
        )
        self.assertIn("vendored EEP.xml", refused[built_in])
        self.assertEqual(refused["A5-02-7A"], "defined twice in the file")
        # Even a direct publish cannot shadow a built-in.
        user_profiles_map = {built_in: accepted["A5-02-7A"]}
        eep_catalog.set_user_profiles(user_profiles_map)
        self.assertIs(
            eep_catalog.generic_profiles()[built_in],
            eep_catalog.built_in_profiles()[built_in],
        )

    def test_unbuildable_layouts_are_refused_with_a_reason(self):
        beyond = {
            **VALID_PROFILE,
            "fields": [{**VALID_PROFILE["fields"][0], "offset": 30}],
        }
        duplicate = {
            **VALID_PROFILE,
            "fields": [VALID_PROFILE["fields"][0], VALID_PROFILE["fields"][0]],
        }
        no_entity = {
            **VALID_PROFILE,
            "fields": [{"shortcut": "X", "kind": "enum", "offset": 0, "size": 1}],
        }
        no_scale = {
            **VALID_PROFILE,
            "fields": [{"shortcut": "TMP", "kind": "value", "offset": 0, "size": 8}],
        }
        _accepted, refused = user_profiles.parse_user_profiles(
            _document(
                beyond,
                {**duplicate, "eep": "A5-02-7B"},
                {**no_entity, "eep": "A5-02-7C"},
                {**no_scale, "eep": "A5-02-7D"},
            )
        )
        self.assertIn("beyond the 32-bit payload", refused["A5-02-7A"])
        self.assertIn("unique", refused["A5-02-7B"])
        self.assertIn("no field would become an entity", refused["A5-02-7C"])
        self.assertIn("range and scale", refused["A5-02-7D"])

    def test_the_schema_is_strict_data_only(self):
        for bad in (
            '{"version": 2, "profiles": []}',
            '{"version": 1}',
            '{"version": 1, "profiles": [{"eep": "A5-02-7A", "description": "x", "fields": [], "code": "import os"}]}',
            '{"version": 1, "profiles": [{"eep": "ZZ-02-30", "description": "x", "fields": [{"shortcut": "TMP", "kind": "value", "offset": 0, "size": 8}]}]}',
            '{"version": 1, "profiles": [{"eep": "A5-02-7A", "description": "x", "fields": [{"shortcut": "TMP", "kind": "value", "offset": 0, "size": 99}]}]}',
            "not json",
            "[]",
        ):
            with (
                self.subTest(bad=bad[:40]),
                self.assertRaises((vol.Invalid, ValueError, TypeError)),
            ):
                user_profiles.parse_user_profiles(bad)
        with self.assertRaises(vol.Invalid):
            user_profiles.parse_user_profiles("x" * (user_profiles.MAX_FILE_BYTES + 1))

    def test_identities_and_row_schema_follow_the_published_profile(self):
        accepted, _ = user_profiles.parse_user_profiles(_document(VALID_PROFILE))
        row = {
            "id": SENDER,
            "platform": "sensor",
            "name": "Synthetic",
            "device_class": "eep",
            "radio_metadata": {"eep": "A5-02-7A"},
        }
        with self.assertRaises(vol.Invalid):
            UI_DEVICE_SCHEMA(row)  # not published: the eep class has no decoder
        eep_catalog.set_user_profiles(accepted)
        self.assertEqual(UI_DEVICE_SCHEMA(row)["radio_metadata"]["eep"], "A5-02-7A")
        self.assertEqual(
            generic_eep_identities(SENDER, "A5-02-7A"),
            (("sensor", "93266483-eep_tmp"), ("binary_sensor", "93266483-eep_pir")),
        )
        eep_catalog.set_user_profiles({})
        self.assertEqual(
            generic_eep_identities(SENDER, "A5-02-7A"), (("sensor", "93266483-eep"),)
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class LoadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ir.async_load(self.hass, read_only=False)
        self.path = user_profiles.user_profiles_path(self.hass)

    async def asyncTearDown(self):
        eep_catalog.set_user_profiles({})
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _issue(self, issue_id):
        return ir.async_get(self.hass).async_get_issue(DOMAIN, issue_id)

    async def test_missing_file_loads_nothing_and_raises_nothing(self):
        report = await user_profiles.async_load_user_profiles(self.hass)
        self.assertFalse(report.present)
        self.assertEqual(eep_catalog.user_profiles(), {})
        self.assertIsNone(self._issue(user_profiles.ISSUE_USER_PROFILES_INVALID))

    async def test_valid_file_publishes_and_a_reload_without_it_unpublishes(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(_document(VALID_PROFILE), encoding="utf-8")
        report = await user_profiles.async_load_user_profiles(self.hass)
        self.assertEqual(report.loaded, ("A5-02-7A",))
        self.assertIn("A5-02-7A", eep_catalog.generic_profiles())
        self.assertTrue(eep_catalog.is_generic_supported("A5-02-7A"))
        self.path.unlink()
        await user_profiles.async_load_user_profiles(self.hass)
        self.assertNotIn("A5-02-7A", eep_catalog.generic_profiles())

    async def test_invalid_file_disables_everything_and_raises_a_repair(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(_document(VALID_PROFILE), encoding="utf-8")
        await user_profiles.async_load_user_profiles(self.hass)
        self.path.write_text('{"version": 1, "profiles": "oops"}', encoding="utf-8")
        report = await user_profiles.async_load_user_profiles(self.hass)
        self.assertIsNotNone(report.error)
        self.assertEqual(eep_catalog.user_profiles(), {})
        issue = self._issue(user_profiles.ISSUE_USER_PROFILES_INVALID)
        self.assertIsNotNone(issue)
        self.assertIn("reason", issue.translation_placeholders)
        # Fixing the file clears the issue on the next load.
        self.path.write_text(_document(VALID_PROFILE), encoding="utf-8")
        await user_profiles.async_load_user_profiles(self.hass)
        self.assertIsNone(self._issue(user_profiles.ISSUE_USER_PROFILES_INVALID))

    async def test_refused_profiles_raise_the_skipped_repair_but_the_rest_loads(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            _document(VALID_PROFILE, {**VALID_PROFILE, "eep": "D2-01-12"}),
            encoding="utf-8",
        )
        report = await user_profiles.async_load_user_profiles(self.hass)
        self.assertEqual(report.loaded, ("A5-02-7A",))
        self.assertEqual(list(report.refused), ["D2-01-12"])
        issue = self._issue(user_profiles.ISSUE_USER_PROFILES_SKIPPED)
        self.assertIsNotNone(issue)
        self.assertIn("D2-01-12", issue.translation_placeholders["eeps"])


if __name__ == "__main__":
    unittest.main()
