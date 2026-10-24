"""Product recipe catalog (M5): a named shortcut, never proof on its own.

``data/products.json`` lets the *declare profile* step be driven by a
product name instead of a typed EEP. A catalog pick goes through the exact
same validated path as typing the EEP by hand -- identity-preservation and
collision checks included -- and is recorded the same way: ``eep_source:
manual``. These tests cover the loader's defensiveness, the platform filter
that keeps unusable recipes out of the picker, and one full options-flow
round trip through each path (catalog pick, custom/manual, and a crafted
cross-platform recipe id that must fail exactly like a wrong typed EEP).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components/enocean_custom"
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom import options_flow
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.device_catalog import (
        PRODUCT_RECIPES,
        TEACH_IN_KINDS,
        _recipe_from_row,
        get_product_recipe,
        recipes_for_platform,
    )
    from custom_components.enocean_custom.identity import ui_device_entity_identities
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SENDER = [5, 140, 103, 208]
DECIMAL = "93087696"
_VALID_ROW = {
    "manufacturer": "Acme",
    "model": "Widget",
    "eep": "A5-12-01",
    "platforms": ["sensor"],
    "channels": [0],
    "teach_in": "4bs",
    "support": "manual",
    "evidence": ["manual capture"],
}


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CatalogFileTests(unittest.TestCase):
    """The bundled data/products.json, loaded for real."""

    def test_every_roadmap_candidate_is_present(self):
        expected = {
            "nodon_asp_2_1_00",
            "nodon_asp_2_1_01",
            "nodon_sin_2_2_01",
            "nodon_sin_2_rs_01",
            "nodon_cws_2_1_01",
            "eltako_ft55",
            "hoppe_secusignal",
            "nexelec_insafe_plus",
            "micropelt_mva005",
            "kieback_md15",
        }
        self.assertEqual(set(PRODUCT_RECIPES), expected)

    def test_every_recipe_has_non_empty_evidence_and_valid_vocabulary(self):
        for recipe_id, recipe in PRODUCT_RECIPES.items():
            with self.subTest(recipe=recipe_id):
                self.assertTrue(recipe.evidence)
                self.assertIn(recipe.teach_in, TEACH_IN_KINDS)
                self.assertIn(recipe.support, ("supported", "manual", "unsupported"))
                # "unsupported" must mean no platform exists at all; "manual"
                # may still have none yet (pending a later roadmap item), but
                # never the reverse -- the picker filter must never disagree.
                if recipe.support == "unsupported":
                    self.assertFalse(recipe.platforms)

    def test_nexelec_and_hoppe_match_the_eeps_this_release_actually_decodes(self):
        self.assertEqual(PRODUCT_RECIPES["nexelec_insafe_plus"].eep, "D2-14-30")
        self.assertEqual(PRODUCT_RECIPES["hoppe_secusignal"].eep, "F6-10-00")
        self.assertEqual(PRODUCT_RECIPES["nodon_sin_2_2_01"].eep, "D2-01-12")

    def test_pending_valve_recipes_are_not_yet_selectable(self):
        # M2 (A5-20-01 / A5-20-06) is a later, experimental item; until it
        # lands these recipes must stay out of every picker.
        for recipe_id in ("micropelt_mva005", "kieback_md15"):
            recipe = PRODUCT_RECIPES[recipe_id]
            self.assertEqual(recipe.platforms, ())
            for platform in ("sensor", "climate", "binary_sensor", "switch", "light"):
                self.assertNotIn(recipe, recipes_for_platform(platform))

    def test_json_file_matches_the_loaded_catalog(self):
        raw = json.loads((COMPONENT / "data/products.json").read_text(encoding="utf-8"))
        self.assertEqual(set(raw["recipes"]), set(PRODUCT_RECIPES))


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class LoaderDefensivenessTests(unittest.TestCase):
    """A malformed row vanishes rather than degrading into a partial recipe."""

    def test_valid_row_round_trips(self):
        recipe = _recipe_from_row("acme_widget", _VALID_ROW)
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.display_name, "Acme Widget")
        self.assertEqual(recipe.channels, (0,))

    def test_missing_or_wrong_typed_fields_are_rejected(self):
        bad_rows = [
            {**_VALID_ROW, "manufacturer": ""},
            {**_VALID_ROW, "model": 12},
            {**_VALID_ROW, "eep": 123},
            {**_VALID_ROW, "platforms": "sensor"},
            {**_VALID_ROW, "channels": ["0"]},
            {**_VALID_ROW, "teach_in": "smart_ack"},
            {**_VALID_ROW, "support": "certified"},
            {**_VALID_ROW, "evidence": []},
            {**_VALID_ROW, "notes": 42},
            {k: v for k, v in _VALID_ROW.items() if k != "manufacturer"},
        ]
        for row in bad_rows:
            with self.subTest(row=row):
                self.assertIsNone(_recipe_from_row("acme_widget", row))

    def test_eep_may_be_absent_for_an_unsupported_entry(self):
        row = {**_VALID_ROW, "eep": None, "platforms": [], "support": "unsupported"}
        recipe = _recipe_from_row("acme_widget", row)
        self.assertIsNotNone(recipe)
        self.assertIsNone(recipe.eep)

    def test_get_product_recipe_rejects_non_strings_and_unknown_ids(self):
        self.assertIsNone(get_product_recipe(None))
        self.assertIsNone(get_product_recipe(42))
        self.assertIsNone(get_product_recipe("does_not_exist"))

    def test_recipes_for_platform_rejects_non_strings(self):
        self.assertEqual(recipes_for_platform(None), ())
        self.assertEqual(recipes_for_platform(123), ())


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeclareProfileCatalogFlowTests(unittest.IsolatedAsyncioTestCase):
    """The options-flow step, driven through the catalog picker."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.registry = er.async_get(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _flow(self, rows):
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: list(rows)},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass, flow.handler = self.hass, entry.entry_id
        return flow, entry

    async def test_picker_only_offers_recipes_matching_the_row_platform(self):
        row = {"id": SENDER, "platform": "binary_sensor", "name": "Detector"}
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        form = await flow.async_step_declare_profile_eep()
        (field,) = [
            value
            for key, value in form["data_schema"].schema.items()
            if str(key) == "catalog_product"
        ]
        offered = {option["value"] for option in field.config["options"]}
        self.assertIn("__custom__", offered)
        self.assertIn("nexelec_insafe_plus", offered)
        self.assertIn("eltako_ft55", offered)
        self.assertNotIn("hoppe_secusignal", offered, "sensor-only recipe")
        self.assertNotIn("nodon_asp_2_1_00", offered, "switch-only recipe")

    async def test_picking_a_product_fills_in_its_eep_like_a_typed_one(self):
        # Shaped like a maintainer row already added as a plain smoke binary
        # sensor, the realistic target for the Nexelec recipe.
        row = {
            "id": SENDER,
            "platform": "binary_sensor",
            "name": "Detector",
            "device_class": "smoke",
        }
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        result = await flow.async_step_declare_profile_eep(
            {"catalog_product": "nexelec_insafe_plus", "manual_eep": ""}
        )
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        updated = result["data"][CONF_UI_DEVICES][0]
        metadata = updated["radio_metadata"]
        self.assertEqual(metadata["eep"], "D2-14-30")
        self.assertEqual(metadata["eep_source"], "manual")
        self.assertEqual(metadata["evidence"], "manual")
        # Same identity-safety guarantee as a hand-typed EEP: the primary
        # entity this row already created keeps its historical unique_id.
        self.assertEqual(
            ui_device_entity_identities(updated)[0],
            ("binary_sensor", f"{DECIMAL}-smoke"),
        )
        self.assertEqual(len(ui_device_entity_identities(updated)), 10)

    async def test_picking_a_switch_product_works_end_to_end_too(self):
        row = {
            "id": SENDER,
            "platform": "switch",
            "name": "Plug",
            "channel": 0,
            "switch_type": "default",
        }
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        result = await flow.async_step_declare_profile_eep(
            {"catalog_product": "nodon_asp_2_1_00", "manual_eep": ""}
        )
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        metadata = result["data"][CONF_UI_DEVICES][0]["radio_metadata"]
        self.assertEqual(metadata["eep"], "D2-01-0A")
        self.assertEqual(metadata["eep_source"], "manual")

    async def test_custom_option_keeps_the_manual_text_field_authoritative(self):
        row = {"id": SENDER, "platform": "binary_sensor", "name": "Detector"}
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        result = await flow.async_step_declare_profile_eep(
            {"catalog_product": "__custom__", "manual_eep": "F6-05-02"}
        )
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        metadata = result["data"][CONF_UI_DEVICES][0]["radio_metadata"]
        self.assertEqual(metadata["eep"], "F6-05-02")

    async def test_omitting_catalog_product_behaves_exactly_like_before(self):
        """Regression guard: the 2.9.8 call shape (manual_eep only) still works."""
        row = {"id": SENDER, "platform": "binary_sensor", "name": "Detector"}
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        result = await flow.async_step_declare_profile_eep({"manual_eep": "F6-05-02"})
        self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
        metadata = result["data"][CONF_UI_DEVICES][0]["radio_metadata"]
        self.assertEqual(metadata["eep"], "F6-05-02")

    async def test_catalog_pick_gets_the_same_collision_guard_as_a_typed_eep(self):
        row = {
            "id": SENDER,
            "platform": "binary_sensor",
            "name": "Detector",
            "device_class": "smoke",
        }
        self.registry.async_get_or_create(
            "binary_sensor", DOMAIN, f"{DECIMAL}-d2-14-30-sensor-fault"
        )
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        refused = await flow.async_step_declare_profile_eep(
            {"catalog_product": "nexelec_insafe_plus", "manual_eep": ""}
        )
        self.assertEqual(refused["type"], FlowResultType.FORM)
        self.assertEqual(refused["errors"], {"base": "unique_id_exists"})

    async def test_unknown_catalog_product_id_is_refused_not_guessed(self):
        row = {"id": SENDER, "platform": "binary_sensor", "name": "Detector"}
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        refused = await flow.async_step_declare_profile_eep(
            {"catalog_product": "not_a_real_recipe", "manual_eep": ""}
        )
        self.assertEqual(refused["type"], FlowResultType.FORM)
        self.assertEqual(refused["errors"], {"manual_eep": "invalid_eep"})

    async def test_no_catalog_field_at_all_when_no_recipe_matches_the_platform(self):
        row = {
            "id": SENDER,
            "platform": "climate",
            "name": "Thermostat",
            "id_switch": SENDER,
            "device_type": "SRC-D08",
            "sensor_entity_id": "sensor.room_temperature",
        }
        flow, _entry = self._flow([row])
        await flow.async_step_declare_profile({"device": "0"})
        form = await flow.async_step_declare_profile_eep()
        fields = {str(key) for key in form["data_schema"].schema}
        self.assertEqual(fields, {"manual_eep"})


if __name__ == "__main__":
    unittest.main()
