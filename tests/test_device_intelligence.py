"""Tests for v2.1 local Device Intelligence."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from typing import ClassVar

_REPO = Path(__file__).resolve().parent.parent
_COMPONENT = _REPO / "custom_components" / "enocean_custom"
sys.path.insert(0, str(_REPO))

try:
    import voluptuous as vol
    import voluptuous_serialize
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom.const import DOMAIN, UI_DEVICE_PLATFORMS
    from custom_components.enocean_custom.device import EnOceanEntity
    from custom_components.enocean_custom.device_intelligence import (
        EEP_IMPLEMENTATIONS,
        MAX_MANUFACTURER_ID,
        PERSISTED_METADATA_FIELDS,
        PRODUCT_CATALOG,
        UNKNOWN_PLACEHOLDER,
        ConfigMode,
        Evidence,
        SupportVerdict,
        apply_manual_eep,
        extract_radio_identity,
        flow_placeholders,
        format_eep,
        metadata_declares_eep,
        parse_commissioning_identity,
        parse_manual_eep,
        recommended_platform,
        reconcile_commissioning_metadata,
        resolve_product,
        safe_metadata,
        support_for,
    )
    from custom_components.enocean_custom.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import RORG
    from custom_components.enocean_custom.options_flow import (
        EnOceanOptionsFlow,
        _parse_commissioning_code,
    )
    from custom_components.enocean_custom.schema import (
        CONF_UI_DEVICES,
        UI_DEVICE_SCHEMA,
        valid_ui_devices,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:
    HA_AVAILABLE = False


# Officially sourced Product IDs, from the EnOcean Alliance DDF repository.
# Entries in its Examples/ folder are deliberately excluded.
EXPECTED_CATALOG = {
    "002D00000004": ("Afriso", "Cositherm 2-Channel", "D2-34-10"),
    "002D0000000A": ("Afriso", "Cositherm 2-Channel", "D2-34-10"),
    "002D00000005": ("Afriso", "Cositherm 6-Channel", "D2-34-10"),
    "002D0000000B": ("Afriso", "Cositherm 6-Channel", "D2-34-10"),
    "001600013045": ("BSC Computer", "eTronic window/door contact", "A5-14-01"),
}

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_DI_STEPS = ("pair_actuator_type", "device_form")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class RadioEvidenceTests(unittest.TestCase):
    """UTE and enriched 4BS declare a profile; ordinary RPS/1BS never do."""

    def test_ute_declares_exact_profile_and_channel(self):
        identity = extract_radio_identity(
            SimpleNamespace(
                sender_int=0x01020304,
                rorg=RORG.UTE,
                rorg_of_eep=RORG.VLD,
                rorg_func=1,
                rorg_type=12,
                rorg_manufacturer=0x123,
                channel=2,
            )
        )
        self.assertEqual(identity.sender_id, (1, 2, 3, 4))
        self.assertEqual(identity.eep, "D2-01-0C")
        self.assertEqual(identity.manufacturer_id, 0x123)
        self.assertEqual(identity.channel, 2)
        self.assertEqual(identity.evidence, Evidence.EXACT)
        self.assertEqual(identity.eep_source, "radio_declared")

    def test_enriched_4bs_declares_exact_profile(self):
        identity = extract_radio_identity(
            SimpleNamespace(
                sender_int=0xAABBCCDD,
                rorg=RORG.BS4,
                contains_eep=True,
                rorg_func=0x12,
                rorg_type=0x01,
                rorg_manufacturer=0x0D,
            )
        )
        self.assertEqual(identity.eep, "A5-12-01")
        self.assertEqual(identity.manufacturer_id, 0x0D)
        self.assertEqual(identity.evidence, Evidence.EXACT)

    def test_rps_f6_and_1bs_d5_stay_profile_unknown(self):
        for rorg in (RORG.RPS, RORG.BS1):
            identity = extract_radio_identity(
                SimpleNamespace(sender_int=1, rorg=rorg, contains_eep=False)
            )
            self.assertIsNone(identity.eep)
            self.assertIsNone(identity.eep_source)
            self.assertEqual(identity.evidence, Evidence.PROFILE_UNKNOWN)
            self.assertEqual(
                safe_metadata(radio=identity)["support"], SupportVerdict.UNKNOWN.value
            )

    def test_plain_4bs_without_teach_in_bit_stays_unknown(self):
        identity = extract_radio_identity(
            SimpleNamespace(sender_int=1, rorg=RORG.BS4, contains_eep=False)
        )
        self.assertIsNone(identity.eep)
        self.assertEqual(identity.evidence, Evidence.PROFILE_UNKNOWN)

    def test_malformed_packets_never_raise(self):
        class BrokenPacket:
            @property
            def sender_int(self):
                raise RuntimeError("malformed computed property")

        for packet in (
            None,
            object(),
            SimpleNamespace(sender_int=-1),
            SimpleNamespace(sender_int="x"),
            SimpleNamespace(sender_int=True),
            SimpleNamespace(sender_int=0x1_0000_0000),
            BrokenPacket(),
        ):
            self.assertIsNone(extract_radio_identity(packet))
        self.assertIsNone(format_eep(0xA5, None, 1))
        self.assertIsNone(format_eep(0xA5, 0x10, 256))
        self.assertIsNone(format_eep(0xA5, True, 1))

    def test_out_of_range_radio_fields_are_dropped_not_masked(self):
        identity = extract_radio_identity(
            SimpleNamespace(
                sender_int=1,
                rorg=RORG.UTE,
                rorg_of_eep=RORG.VLD,
                rorg_func=1,
                rorg_type=12,
                rorg_manufacturer=0x800,
                channel=999,
            )
        )
        self.assertIsNone(identity.manufacturer_id)
        self.assertIsNone(identity.channel)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CommissioningLabelTests(unittest.TestCase):
    """Alliance 30S/1P parsing is strict and drops every security container."""

    def test_product_id_is_split_into_manufacturer_and_reference(self):
        parsed = parse_commissioning_identity("30S000001020304+1P0123AABBCCDD")
        self.assertEqual(parsed.sender_id, (1, 2, 3, 4))
        self.assertEqual(parsed.product_id, "0123AABBCCDD")
        self.assertEqual(parsed.manufacturer_id, 0x123)
        self.assertEqual(parsed.product_reference, 0xAABBCCDD)
        self.assertEqual(parsed.evidence, Evidence.ASSISTED)

    def test_security_containers_are_discarded_and_never_persisted(self):
        parsed = parse_commissioning_identity(
            "30S000001020304+1P0123AABBCCDD+10ZSECRETA+11ZKEYB+13ZOTHERC"
        )
        metadata = safe_metadata(commissioning=parsed)
        rendered = repr(parsed) + repr(metadata) + repr(flow_placeholders(metadata))
        for secret in ("SECRETA", "KEYB", "OTHERC", "10Z", "11Z", "13Z"):
            self.assertNotIn(secret, rendered)

    def test_48_bit_eurid_is_rejected_not_truncated(self):
        self.assertIsNone(
            parse_commissioning_identity("30S010203040506+1P0123AABBCCDD")
        )
        self.assertIsNone(_parse_commissioning_code("30S010203040506+1P0123AABBCCDD"))

    def test_reserved_manufacturer_bits_are_rejected_not_masked(self):
        # 0x0800 masked with 0x7FF would silently become 0x000.
        self.assertIsNone(
            parse_commissioning_identity("30S000001020304+1P0800AABBCCDD")
        )
        self.assertIsNone(
            parse_commissioning_identity("30S000001020304+1PFFFFAABBCCDD")
        )
        boundary = parse_commissioning_identity("30S000001020304+1P07FFAABBCCDD")
        self.assertEqual(boundary.manufacturer_id, MAX_MANUFACTURER_ID)

    def test_strict_typed_id_and_malformed_labels(self):
        self.assertEqual(
            parse_commissioning_identity("01:02:03:04").sender_id, (1, 2, 3, 4)
        )
        self.assertIsNone(parse_commissioning_identity("01:02:03:04").product_id)
        for bad in (
            None,
            42,
            "",
            "1:2:3:4",
            "01:02:03",
            "30S000001020304",
            "1P0123AABBCCDD",
            "30S000001020304+30S000001020305+1P0123AABBCCDD",
            "30S000001020304+1P0123AABBCCDD+1P0123AABBCCDE",
            "30S00000102030+1P0123AABBCCDD",
            "30S000001020304 1P0123AABBCCDD",
        ):
            self.assertIsNone(parse_commissioning_identity(bad), bad)

    def test_options_flow_helper_returns_only_the_sender(self):
        self.assertEqual(
            _parse_commissioning_code("30S000001020304+1P0123AABBCCDD"), [1, 2, 3, 4]
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ProductCatalogTests(unittest.TestCase):
    """The bundled catalog is small, official and never certifies decoding."""

    def test_catalog_is_exactly_the_officially_sourced_product_ids(self):
        self.assertEqual(set(PRODUCT_CATALOG), set(EXPECTED_CATALOG))
        for product_id, (manufacturer, model, eep) in EXPECTED_CATALOG.items():
            entry = PRODUCT_CATALOG[product_id]
            self.assertEqual(entry.manufacturer, manufacturer)
            self.assertEqual(entry.model, model)
            self.assertEqual(entry.model_id, product_id)
            self.assertEqual(entry.eep, eep)
            self.assertIn("enocean-alliance-ddf", entry.source)

    def test_catalog_manufacturer_fields_respect_the_11_bit_bound(self):
        for product_id in PRODUCT_CATALOG:
            manufacturer = int(product_id[:4], 16)
            self.assertLessEqual(manufacturer, MAX_MANUFACTURER_ID, product_id)

    def test_identified_products_can_still_be_unsupported(self):
        parsed = parse_commissioning_identity("30S000001020304+1P002D00000004")
        metadata = safe_metadata(commissioning=parsed)
        self.assertEqual(metadata["eep"], "D2-34-10")
        self.assertEqual(metadata["eep_source"], "product_declared")
        self.assertEqual(metadata["evidence"], Evidence.ASSISTED.value)
        self.assertEqual(metadata["support"], SupportVerdict.UNSUPPORTED.value)
        self.assertIsNone(recommended_platform(metadata))
        product = resolve_product(metadata)
        self.assertEqual(product.manufacturer, "Afriso")
        self.assertEqual(product.model, "Cositherm 2-Channel")

    def test_unknown_product_ids_stay_unknown(self):
        metadata = safe_metadata(
            commissioning=parse_commissioning_identity("30S000001020304+1P0123AABBCCDD")
        )
        self.assertIsNone(resolve_product(metadata))
        self.assertNotIn("eep", metadata)
        self.assertEqual(metadata["support"], SupportVerdict.UNKNOWN.value)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ImplementationMatrixTests(unittest.TestCase):
    """The matrix reports configuration mode, never hardware certification."""

    def test_matrix_platforms_exist_and_modes_are_consistent(self):
        for eep, implementation in EEP_IMPLEMENTATIONS.items():
            self.assertRegex(eep, r"^[0-9A-F]{2}-[0-9A-F]{2}-[0-9A-F]{2}$")
            self.assertTrue(implementation.platforms)
            for platform in implementation.platforms:
                self.assertIn(platform, (*UI_DEVICE_PLATFORMS, "climate"))
            if implementation.mode in (ConfigMode.MANUAL, ConfigMode.YAML_ONLY):
                self.assertIsNone(implementation.recommended_platform, eep)
                self.assertEqual(implementation.support, SupportVerdict.MANUAL, eep)
            else:
                self.assertEqual(implementation.support, SupportVerdict.SUPPORTED, eep)

    def test_ambiguous_profiles_are_never_preselected(self):
        for eep in ("F6-02-01", "F6-02-02", "D2-01-12", "A5-10-06", "A5-20-04"):
            self.assertIsNone(recommended_platform({"eep": eep}), eep)
            self.assertEqual(support_for(eep), SupportVerdict.MANUAL, eep)

    def test_unambiguous_profiles_are_preselected(self):
        for eep in ("F6-10-00", "D5-00-01", "A5-12-01", "D2-01-0B"):
            self.assertEqual(recommended_platform({"eep": eep}), "sensor", eep)
            self.assertEqual(support_for(eep), SupportVerdict.SUPPORTED, eep)
        self.assertEqual(recommended_platform({"eep": "A5-38-08"}), "light")

    def test_support_separates_evidence_from_implementation(self):
        self.assertEqual(support_for(None), SupportVerdict.UNKNOWN)
        self.assertEqual(support_for(""), SupportVerdict.UNKNOWN)
        self.assertEqual(support_for("FF-FF-FF"), SupportVerdict.UNSUPPORTED)
        self.assertEqual(support_for("A5-12-01"), SupportVerdict.SUPPORTED)
        # A manually chosen profile is never promoted to automatic support.
        self.assertEqual(support_for("A5-12-01", manual=True), SupportVerdict.MANUAL)
        manual = safe_metadata(manual_eep="A5-12-01")
        self.assertEqual(manual["evidence"], Evidence.MANUAL.value)
        self.assertEqual(manual["support"], SupportVerdict.MANUAL.value)
        self.assertEqual(manual["eep_source"], "manual")

    def test_documented_matrix_matches_the_code(self):
        table = (_REPO / "docs" / "device-intelligence.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"`([0-9A-F]{2}-[0-9A-F]{2}-[0-9A-F]{2})`", table))
        self.assertTrue(set(EEP_IMPLEMENTATIONS) <= documented)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ManualEepAssertionTests(unittest.TestCase):
    """An operator assertion is canonicalized, bounded, and never proof."""

    UNKNOWN_QR = "30S000001020304+1P0123AABBCCDD"

    def test_canonical_form_accepts_lowercase_and_surrounding_space(self):
        self.assertEqual(parse_manual_eep("a5-12-01"), "A5-12-01")
        self.assertEqual(parse_manual_eep("  d2-01-0b\n"), "D2-01-0B")
        self.assertEqual(parse_manual_eep("A5-12-01"), "A5-12-01")
        self.assertEqual(parse_manual_eep("f6-02-01"), "F6-02-01")

    def test_invalid_values_are_rejected_never_coerced(self):
        for bad in (
            None,
            True,
            False,
            0,
            0xA5,
            1.0,
            [],
            {},
            ["A5-12-01"],
            b"A5-12-01",
            "",
            "   ",
            "A5-12",
            "A5-12-01-02",
            "A512-01",
            "A5_12_01",
            "A5 12 01",
            "G5-12-01",
            "A5-12-0",
            "A5-12-011",
            "A5-12-01;DROP",
            "0xA5-0x12-0x01",
        ):
            self.assertIsNone(parse_manual_eep(bad), bad)

    def test_an_implemented_profile_is_manual_never_supported(self):
        metadata = apply_manual_eep(None, "a5-12-01")
        self.assertEqual(metadata["eep"], "A5-12-01")
        self.assertEqual(metadata["eep_source"], "manual")
        self.assertEqual(metadata["evidence"], Evidence.MANUAL.value)
        self.assertEqual(metadata["support"], SupportVerdict.MANUAL.value)
        # A5-12-01 maps to exactly one platform, but a claim never preselects.
        self.assertIsNone(recommended_platform(metadata))
        self.assertTrue(set(metadata) <= PERSISTED_METADATA_FIELDS)

    def test_an_unimplemented_profile_is_unsupported(self):
        for eep in ("D2-34-10", "A5-14-01", "FF-FF-FF"):
            metadata = apply_manual_eep(None, eep.lower())
            self.assertEqual(metadata["eep"], eep, eep)
            self.assertEqual(metadata["evidence"], Evidence.MANUAL.value, eep)
            self.assertEqual(metadata["support"], SupportVerdict.UNSUPPORTED.value, eep)
            self.assertNotIn(eep, EEP_IMPLEMENTATIONS)

    def test_an_empty_or_invalid_assertion_changes_nothing(self):
        unknown = safe_metadata(
            radio=extract_radio_identity(
                SimpleNamespace(sender_int=0x01020304, rorg=RORG.RPS)
            )
        )
        for value in ("", "   ", None, True, 42, "A5-12", ["A5-12-01"]):
            self.assertEqual(apply_manual_eep(unknown, value), unknown, value)
        self.assertEqual(unknown["evidence"], Evidence.PROFILE_UNKNOWN.value)
        self.assertEqual(unknown["support"], SupportVerdict.UNKNOWN.value)
        self.assertNotIn("eep", unknown)

    def test_safe_qr_metadata_survives_the_assertion(self):
        qr = safe_metadata(commissioning=parse_commissioning_identity(self.UNKNOWN_QR))
        self.assertNotIn("eep", qr)  # this Product ID is not in the catalog
        metadata = apply_manual_eep(qr, "f6-02-01")
        self.assertEqual(metadata["sender_id"], [1, 2, 3, 4])
        self.assertEqual(metadata["manufacturer_id"], 0x123)
        self.assertEqual(metadata["product_id"], "0123AABBCCDD")
        self.assertEqual(metadata["product_reference"], 0xAABBCCDD)
        self.assertEqual(metadata["eep"], "F6-02-01")
        self.assertEqual(metadata["eep_source"], "manual")
        self.assertEqual(metadata["support"], SupportVerdict.MANUAL.value)
        self.assertTrue(set(metadata) <= PERSISTED_METADATA_FIELDS)
        UI_DEVICE_SCHEMA(
            {
                "id": [1, 2, 3, 4],
                "platform": "switch",
                "name": "manual",
                "radio_metadata": metadata,
            }
        )

    def test_payloads_and_forged_strings_never_re_enter(self):
        metadata = apply_manual_eep(
            {
                "sender_id": [1, 2, 3, 4],
                "raw_qr": "30S000001020304+1P0123AABBCCDD+10ZSECRETA",
                "key": "deadbeefc0ffee",
                "manufacturer": "Afriso",
                "model": "Cositherm 2-Channel",
            },
            "A5-12-01",
        )
        self.assertTrue(set(metadata) <= PERSISTED_METADATA_FIELDS)
        for leaked in ("SECRETA", "deadbeef", "Afriso", "Cositherm", "10Z", "raw_qr"):
            self.assertNotIn(leaked, repr(metadata))

    def test_a_manufacturer_conflict_is_never_upgraded(self):
        conflicted = {
            "sender_id": [1, 2, 3, 4],
            "manufacturer_id": 0x123,
            "product_id": "002D00000004",
            "manufacturer_conflict": True,
            "evidence": Evidence.ASSISTED.value,
            "support": SupportVerdict.UNKNOWN.value,
        }
        metadata = apply_manual_eep(conflicted, "a5-12-01")
        self.assertEqual(metadata["eep"], "A5-12-01")
        self.assertEqual(metadata["support"], SupportVerdict.UNKNOWN.value)
        self.assertTrue(metadata["manufacturer_conflict"])
        self.assertIsNone(recommended_platform(metadata))
        self.assertIsNone(resolve_product(metadata))

    def test_a_conflicting_declared_profile_refuses_the_assertion(self):
        qr = parse_commissioning_identity("30S000001020304+1P002D00000004")
        radio = extract_radio_identity(
            SimpleNamespace(
                sender_int=0x01020304,
                rorg=RORG.BS4,
                contains_eep=True,
                rorg_func=0x12,
                rorg_type=0x01,
                rorg_manufacturer=0x124,
            )
        )
        conflicted = safe_metadata(radio, qr)
        self.assertEqual(apply_manual_eep(conflicted, "F6-02-01"), conflicted)

    def test_declared_profiles_are_never_overwritten(self):
        for source in ("radio_declared", "product_declared", "observed", None):
            declared = {
                "sender_id": [1, 2, 3, 4],
                "eep": "A5-12-01",
                "evidence": Evidence.EXACT.value,
                "support": SupportVerdict.SUPPORTED.value,
            }
            if source is not None:
                declared["eep_source"] = source
            self.assertTrue(metadata_declares_eep(declared), source)
            self.assertEqual(apply_manual_eep(declared, "F6-02-01"), declared, source)
            self.assertEqual(apply_manual_eep(declared, "a5-38-08"), declared, source)

    def test_declaration_detection_ignores_unusable_shapes(self):
        for absent in (
            None,
            "nonsense",
            42,
            {},
            {"eep": ""},
            {"eep": 5},
            {"eep": None},
            {"eep_source": "radio_declared"},
            {"eep": "A5-12-01", "eep_source": "manual"},
        ):
            self.assertFalse(metadata_declares_eep(absent), absent)

    def test_a_previous_manual_assertion_can_be_corrected(self):
        first = apply_manual_eep({"sender_id": [1, 2, 3, 4]}, "A5-12-01")
        second = apply_manual_eep(first, "d2-01-12")
        self.assertEqual(second["eep"], "D2-01-12")
        self.assertEqual(second["eep_source"], "manual")
        self.assertEqual(second["sender_id"], [1, 2, 3, 4])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ManualEepOptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    """The device_form exposes the assertion without weakening any proof."""

    UNKNOWN_QR = "30S000001020304+1P0123AABBCCDD"
    CATALOGED_QR = "30S000001020304+1P002D00000004"

    async def asyncSetUp(self) -> None:
        """Create the minimal registries an options flow touches."""
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})

    async def asyncTearDown(self) -> None:
        """Stop Home Assistant and discard its temporary config directory."""
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _bind_flow(self):
        """Bind a real options flow to a fresh config entry."""
        entry = ConfigEntry(
            data={"device": "/dev/test-manual-eep"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        flow = EnOceanOptionsFlow()
        flow.hass = self.hass
        flow.handler = entry.entry_id
        return flow, entry

    @staticmethod
    def _fields(result) -> list[str]:
        """Return the submitted field names of a rendered form."""
        return [str(key) for key in result["data_schema"].schema]

    @staticmethod
    def _assert_ws_serializable(result) -> None:
        """Fail when the form would crash the websocket flow handler."""
        voluptuous_serialize.convert(
            result["data_schema"], custom_serializer=cv.custom_serializer
        )

    async def test_the_field_is_offered_and_serializable_when_nothing_is_declared(self):
        flow, _entry = self._bind_flow()
        result = await flow.async_step_qr_code({"qr_code": self.UNKNOWN_QR})
        self.assertEqual(result["step_id"], "device_form")
        self.assertIn("manual_eep", self._fields(result))
        self._assert_ws_serializable(result)

    async def test_a_declared_profile_hides_the_field_and_ignores_a_forgery(self):
        flow, _entry = self._bind_flow()
        result = await flow.async_step_qr_code({"qr_code": self.CATALOGED_QR})
        self.assertEqual(result["step_id"], "device_form")
        self.assertNotIn("manual_eep", self._fields(result))
        self._assert_ws_serializable(result)
        details = await flow.async_step_device_form(
            {"platform": "switch", "name": "Cositherm", "manual_eep": "F6-02-01"}
        )
        self.assertEqual(details["step_id"], "device_details")
        self.assertEqual(flow._radio_metadata["eep"], "D2-34-10")
        self.assertEqual(flow._radio_metadata["eep_source"], "product_declared")
        self.assertEqual(flow._radio_metadata["evidence"], Evidence.ASSISTED.value)

    async def test_an_invalid_assertion_redisplays_the_form_with_its_error(self):
        flow, _entry = self._bind_flow()
        await flow.async_step_qr_code({"qr_code": self.UNKNOWN_QR})
        result = await flow.async_step_device_form(
            {"platform": "switch", "name": "Rocker", "manual_eep": "A5-12"}
        )
        self.assertEqual(result["step_id"], "device_form")
        self.assertEqual(result["errors"], {"manual_eep": "invalid_eep"})
        self.assertNotIn("eep", flow._radio_metadata)
        self.assertIn("manual_eep", self._fields(result))
        self._assert_ws_serializable(result)

    async def test_an_empty_assertion_keeps_the_profile_unknown(self):
        flow, _entry = self._bind_flow()
        flow._captured_id = [1, 2, 3, 4]
        flow._radio_metadata = safe_metadata(
            radio=extract_radio_identity(
                SimpleNamespace(sender_int=0x01020304, rorg=RORG.RPS)
            )
        )
        details = await flow.async_step_device_form(
            {"platform": "switch", "name": "Rocker", "manual_eep": "   "}
        )
        self.assertEqual(details["step_id"], "device_details")
        self.assertNotIn("eep", flow._radio_metadata)
        self.assertEqual(
            flow._radio_metadata["evidence"], Evidence.PROFILE_UNKNOWN.value
        )
        self.assertEqual(flow._radio_metadata["support"], SupportVerdict.UNKNOWN.value)

    async def test_a_valid_assertion_is_persisted_beside_the_safe_qr_fields(self):
        flow, _entry = self._bind_flow()
        await flow.async_step_qr_code({"qr_code": self.UNKNOWN_QR})
        details = await flow.async_step_device_form(
            {"platform": "switch", "name": "Manual rocker", "manual_eep": "a5-12-01"}
        )
        self.assertEqual(details["step_id"], "device_details")
        created = await flow.async_step_device_details(
            {"channel": 0, "switch_type": "default"}
        )
        self.assertEqual(created["type"], FlowResultType.CREATE_ENTRY)
        metadata = created["data"][CONF_UI_DEVICES][0]["radio_metadata"]
        self.assertEqual(metadata["eep"], "A5-12-01")
        self.assertEqual(metadata["eep_source"], "manual")
        self.assertEqual(metadata["evidence"], Evidence.MANUAL.value)
        self.assertEqual(metadata["support"], SupportVerdict.MANUAL.value)
        self.assertEqual(metadata["sender_id"], [1, 2, 3, 4])
        self.assertEqual(metadata["product_id"], "0123AABBCCDD")
        self.assertEqual(metadata["product_reference"], 0xAABBCCDD)
        self.assertEqual(metadata["manufacturer_id"], 0x123)
        self.assertTrue(set(metadata) <= PERSISTED_METADATA_FIELDS)
        self.assertEqual(len(valid_ui_devices(created["data"][CONF_UI_DEVICES])), 1)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ConflictTests(unittest.TestCase):
    """A manufacturer conflict blocks every model claim and preselection."""

    def _conflicting_pair(self):
        qr = parse_commissioning_identity("30S000001020304+1P002D00000004")
        radio = extract_radio_identity(
            SimpleNamespace(
                sender_int=0x01020304,
                rorg=RORG.BS4,
                contains_eep=True,
                rorg_func=0x12,
                rorg_type=0x01,
                rorg_manufacturer=0x124,
            )
        )
        return qr, radio

    def test_conflict_downgrades_support_and_suppresses_the_product(self):
        qr, radio = self._conflicting_pair()
        metadata = safe_metadata(radio, qr)
        self.assertTrue(metadata["manufacturer_conflict"])
        self.assertEqual(metadata["support"], SupportVerdict.UNKNOWN.value)
        self.assertIsNone(recommended_platform(metadata))
        self.assertIsNone(resolve_product(metadata))
        self.assertEqual(
            flow_placeholders(metadata)["radio_conflict"], "manufacturer_conflict"
        )
        self.assertEqual(
            flow_placeholders(metadata)["radio_model"], UNKNOWN_PLACEHOLDER
        )

    def test_reconcile_flags_the_same_conflict(self):
        qr, radio = self._conflicting_pair()
        reconciled = reconcile_commissioning_metadata(qr, safe_metadata(radio=radio))
        self.assertTrue(reconciled["manufacturer_conflict"])
        self.assertEqual(reconciled["support"], SupportVerdict.UNKNOWN.value)
        self.assertIsNone(recommended_platform(reconciled))
        self.assertIsNone(resolve_product(reconciled))

    def test_reconcile_ignores_a_capture_from_another_sender(self):
        qr = parse_commissioning_identity("30S000001020304+1P0123AABBCCDD")
        other = safe_metadata(
            radio=extract_radio_identity(
                SimpleNamespace(
                    sender_int=0x0A0B0C0D,
                    rorg=RORG.BS4,
                    contains_eep=True,
                    rorg_func=0x12,
                    rorg_type=0x01,
                    rorg_manufacturer=0x123,
                )
            )
        )
        reconciled = reconcile_commissioning_metadata(qr, other)
        self.assertNotIn("eep", reconciled)
        self.assertNotIn("manufacturer_conflict", reconciled)

    def test_matching_sender_promotes_the_radio_profile(self):
        qr = parse_commissioning_identity("30S000001020304+1P0123AABBCCDD")
        radio = safe_metadata(
            radio=extract_radio_identity(
                SimpleNamespace(
                    sender_int=0x01020304,
                    rorg=RORG.BS4,
                    contains_eep=True,
                    rorg_func=0x12,
                    rorg_type=0x01,
                    rorg_manufacturer=0x123,
                )
            )
        )
        reconciled = reconcile_commissioning_metadata(qr, radio)
        self.assertEqual(reconciled["eep"], "A5-12-01")
        self.assertEqual(reconciled["evidence"], Evidence.EXACT.value)
        self.assertEqual(reconciled["support"], SupportVerdict.SUPPORTED.value)
        self.assertEqual(recommended_platform(reconciled), "sensor")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PersistenceSchemaTests(unittest.TestCase):
    """Hand-editable options can never forge identity or smuggle payloads."""

    OLD_ROW: ClassVar[dict] = {"id": [1, 2, 3, 4], "platform": "switch", "name": "old"}

    def test_safe_metadata_only_emits_persistable_fields(self):
        cases = [
            safe_metadata(),
            safe_metadata(manual_eep="A5-12-01"),
            safe_metadata(
                commissioning=parse_commissioning_identity(
                    "30S000001020304+1P002D00000004"
                )
            ),
            safe_metadata(
                radio=extract_radio_identity(
                    SimpleNamespace(sender_int=0x01020304, rorg=RORG.RPS)
                )
            ),
        ]
        for metadata in cases:
            self.assertTrue(set(metadata) <= PERSISTED_METADATA_FIELDS, metadata)
            for forgeable in ("manufacturer", "model", "model_id"):
                self.assertNotIn(forgeable, metadata)
            UI_DEVICE_SCHEMA({**self.OLD_ROW, "radio_metadata": metadata})

    def test_rows_without_metadata_stay_valid(self):
        self.assertIsNone(UI_DEVICE_SCHEMA(self.OLD_ROW)["radio_metadata"])
        self.assertEqual(len(valid_ui_devices([self.OLD_ROW])), 1)

    def test_new_rows_round_trip(self):
        row = {
            **self.OLD_ROW,
            "radio_metadata": {
                "sender_id": [1, 2, 3, 4],
                "eep": "A5-12-01",
                "evidence": "exact",
                "support": "supported",
                "eep_source": "radio_declared",
            },
        }
        self.assertEqual(UI_DEVICE_SCHEMA(row)["radio_metadata"]["eep"], "A5-12-01")
        self.assertEqual(len(valid_ui_devices([row])), 1)

    def test_forged_identity_strings_are_rejected(self):
        for forged in (
            {"manufacturer": "Afriso"},
            {"model": "Cositherm 2-Channel"},
            {"model_id": "002D00000004"},
            {"raw_qr": "30S000001020304+1P0123AABBCCDD+10ZSECRET"},
            {"key": "deadbeef"},
            {"manufacturer_id": 0x800},
            {"eep": "a5-12-01"},
            {"evidence": "certified"},
            {"support": "certified"},
        ):
            with self.assertRaises(vol.Invalid, msg=forged):
                UI_DEVICE_SCHEMA({**self.OLD_ROW, "radio_metadata": forged})
            self.assertEqual(
                valid_ui_devices([{**self.OLD_ROW, "radio_metadata": forged}]), []
            )

    def test_metadata_sender_must_match_its_own_device_row(self):
        mismatched = {**self.OLD_ROW, "radio_metadata": {"sender_id": [9, 9, 9, 9]}}
        with self.assertRaises(vol.Invalid):
            UI_DEVICE_SCHEMA(mismatched)
        self.assertEqual(valid_ui_devices([mismatched]), [])
        matched = {**self.OLD_ROW, "radio_metadata": {"sender_id": [1, 2, 3, 4]}}
        self.assertEqual(len(valid_ui_devices([matched])), 1)

    def test_non_object_metadata_is_rejected(self):
        self.assertEqual(
            valid_ui_devices([{**self.OLD_ROW, "radio_metadata": "bad"}]), []
        )


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeviceRegistryTests(unittest.TestCase):
    """Entities group by sender; only a cataloged Product ID names a model."""

    def test_sender_grouping_without_metadata(self):
        entity = EnOceanEntity([1, 2, 3, 4], "Device")
        info = entity.device_info
        self.assertEqual(info["identifiers"], {("enocean_custom", "01020304")})
        self.assertEqual(info["name"], "Device")
        for invented in ("manufacturer", "model", "model_id"):
            self.assertNotIn(invented, info)

    def test_an_exact_eep_alone_never_becomes_a_model(self):
        entity = EnOceanEntity([1, 2, 3, 4], "Device").set_radio_metadata(
            {"eep": "A5-12-01", "evidence": "exact", "support": "supported"}
        )
        info = entity.device_info
        self.assertEqual(info["identifiers"], {("enocean_custom", "01020304")})
        self.assertNotIn("model", info)
        self.assertNotIn("manufacturer", info)

    def test_cataloged_product_id_populates_the_registry(self):
        entity = EnOceanEntity([1, 2, 3, 4], "Device").set_radio_metadata(
            {"product_id": "002D00000004", "evidence": "assisted"}
        )
        info = entity.device_info
        self.assertEqual(info["manufacturer"], "Afriso")
        self.assertEqual(info["model"], "Cositherm 2-Channel")
        self.assertEqual(info["model_id"], "002D00000004")

    def test_conflict_suppresses_the_registry_model(self):
        entity = EnOceanEntity([1, 2, 3, 4], "Device").set_radio_metadata(
            {"product_id": "002D00000004", "manufacturer_conflict": True}
        )
        self.assertNotIn("model", entity.device_info)

    def test_incomplete_identity_yields_no_device(self):
        for identifier in ([], [1, 2, 3], None, "01020304"):
            self.assertIsNone(EnOceanEntity(identifier, "Device").device_info)

    def test_unknown_metadata_shapes_are_ignored(self):
        entity = EnOceanEntity([1, 2, 3, 4], "Device").set_radio_metadata("nonsense")
        self.assertIsNotNone(entity.device_info)
        self.assertNotIn("model", entity.device_info)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class FlowPlaceholderTests(unittest.TestCase):
    """Flow placeholders stay language-neutral and never leak a payload."""

    def test_empty_metadata_yields_neutral_unknowns(self):
        placeholders = flow_placeholders(None)
        self.assertEqual(placeholders["radio_eep"], UNKNOWN_PLACEHOLDER)
        self.assertEqual(placeholders["radio_manufacturer"], UNKNOWN_PLACEHOLDER)
        self.assertEqual(placeholders["radio_model"], UNKNOWN_PLACEHOLDER)
        self.assertEqual(placeholders["radio_evidence"], "profile_unknown")
        self.assertEqual(placeholders["radio_support"], "unknown")

    def test_values_are_identifiers_proper_nouns_or_stable_tokens(self):
        metadata = safe_metadata(
            commissioning=parse_commissioning_identity("30S000001020304+1P002D00000004")
        )
        placeholders = flow_placeholders(metadata)
        self.assertEqual(placeholders["radio_manufacturer"], "Afriso")
        self.assertEqual(placeholders["radio_manufacturer_id"], "02D")
        self.assertEqual(placeholders["radio_model"], "Cositherm 2-Channel")
        self.assertEqual(placeholders["radio_eep"], "D2-34-10")
        self.assertEqual(placeholders["radio_support"], "unsupported")
        for value in placeholders.values():
            self.assertIsInstance(value, str)
            self.assertNotIn("\n", value)

    def test_corrupt_tokens_fall_back_to_the_safest_value(self):
        placeholders = flow_placeholders(
            {"evidence": {"nested": True}, "support": 7, "manufacturer_id": "x"}
        )
        self.assertEqual(placeholders["radio_evidence"], "profile_unknown")
        self.assertEqual(placeholders["radio_support"], "unknown")
        self.assertEqual(placeholders["radio_manufacturer_id"], UNKNOWN_PLACEHOLDER)


class TranslationTests(unittest.TestCase):
    """strings.json, en.json and fr.json stay structurally identical."""

    def _load(self, name):
        return json.loads((_COMPONENT / name).read_text(encoding="utf-8"))

    @staticmethod
    def _keys(node, prefix=""):
        if not isinstance(node, dict):
            return {prefix}
        keys = set()
        for key, value in node.items():
            keys |= TranslationTests._keys(value, f"{prefix}/{key}")
        return keys

    def test_all_three_files_share_the_same_keys(self):
        base = self._keys(self._load("strings.json"))
        self.assertEqual(base, self._keys(self._load("translations/en.json")))
        self.assertEqual(base, self._keys(self._load("translations/fr.json")))

    def test_every_file_uses_the_same_placeholders(self):
        expected = {
            "captured_id",
            "radio_eep",
            "radio_manufacturer",
            "radio_manufacturer_id",
            "radio_model",
            "radio_evidence",
            "radio_support",
            "radio_conflict",
        }
        for name in ("strings.json", "translations/en.json", "translations/fr.json"):
            steps = self._load(name)["options"]["step"]
            for step in _DI_STEPS:
                found = set(_PLACEHOLDER.findall(steps[step]["description"]))
                self.assertEqual(found, expected, f"{name}:{step}")

    @unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
    def test_the_flow_supplies_every_declared_placeholder(self):
        supplied = {"captured_id", *flow_placeholders(None)}
        steps = self._load("strings.json")["options"]["step"]
        for step in _DI_STEPS:
            self.assertTrue(
                set(_PLACEHOLDER.findall(steps[step]["description"])) <= supplied
            )

    def test_french_labels_are_translated_not_english_sentences(self):
        english = self._load("translations/en.json")["options"]["step"]
        french = self._load("translations/fr.json")["options"]["step"]
        for step in _DI_STEPS:
            en_text = english[step]["description"]
            fr_text = french[step]["description"]
            self.assertNotEqual(en_text, fr_text)
            for label in ("Profil EEP", "Fabricant", "Niveau de preuve", "Conflit"):
                self.assertIn(label, fr_text, step)
            for label in ("Manufacturer:", "Evidence:", "Support:", "Conflict:"):
                self.assertNotIn(label, fr_text, step)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DiagnosticsPrivacyTests(unittest.IsolatedAsyncioTestCase):
    """Exportable diagnostics carry aggregates only, never device identity."""

    async def test_radio_product_and_ui_identifiers_are_not_exported(self):
        entry = SimpleNamespace(
            data={
                "device": "/dev/private",
                "id": [1, 2, 3, 4],
                "product_id": "002D00000004",
                "product_reference": 0xAABBCCDD,
                "manufacturer_id": 0x2D,
                "radio_metadata": {"sender_id": [1, 2, 3, 4], "eep": "A5-12-01"},
                "ui_devices": [{"id": [1, 2, 3, 4], "name": "Kitchen rocker"}],
            },
            options={"ui_devices": [{"id": [1, 2, 3, 4], "name": "Kitchen rocker"}]},
            runtime_data=SimpleNamespace(
                diagnostics=lambda: {"available": True, "received_radio_packets": 12}
            ),
        )
        exported = await async_get_config_entry_diagnostics(None, entry)
        rendered = repr(exported)
        for secret in (
            "002D00000004",
            "[1, 2, 3, 4]",
            "/dev/private",
            "Kitchen rocker",
            "Afriso",
            "Cositherm",
            "A5-12-01",
        ):
            self.assertNotIn(secret, rendered)
        self.assertNotIn("ui_devices", repr(exported["config_entry"].values()))
        self.assertEqual(exported["dongle"]["received_radio_packets"], 12)

    async def test_options_are_never_exported_at_all(self):
        entry = SimpleNamespace(
            data={"device": "/dev/private"},
            options={"ui_devices": [{"id": [9, 9, 9, 9], "name": "Bedroom"}]},
            runtime_data=SimpleNamespace(diagnostics=dict),
        )
        exported = await async_get_config_entry_diagnostics(None, entry)
        self.assertNotIn("Bedroom", repr(exported))
        self.assertNotIn("options", exported)


if __name__ == "__main__":
    unittest.main()
