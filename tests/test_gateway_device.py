"""The dongle is a device of its own: version facts, repeater read-back, hub sensors.

CO_RD_VERSION and CO_RD_REPEATER are ESP3 common commands addressed to the
dongle: they never leave the serial link. Their responses carry no command
identifier, so the communicator correlates them by the order of physical
writes, exactly as it already does for CO_RD_IDBASE.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare unit-test environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.enocean_custom import (
        async_register_gateway_device,
        binary_sensor,
        sensor,
    )
    from custom_components.enocean_custom import dongle as dongle_module
    from custom_components.enocean_custom.const import DOMAIN, SIGNAL_GATEWAY_UPDATE
    from custom_components.enocean_custom.dongle import (
        CO_RD_REPEATER,
        CO_RD_VERSION,
        EnOceanDongle,
        GatewayInfo,
        parse_repeater_response,
        parse_version_response,
    )
    from custom_components.enocean_custom.enocean_library.communicators.communicator import (
        Communicator,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        RETURN_CODE,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        Packet,
        ResponsePacket,
    )
    from custom_components.enocean_custom.entity import EnOceanEntity
    from custom_components.enocean_custom.identity import (
        gateway_device_identifier,
        gateway_unique_id,
    )
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

VERSION_DATA = [
    2,
    11,
    1,
    0,  # APP_VERSION main.beta.alpha.build
    2,
    6,
    3,
    0,  # API_VERSION
    0xFF,
    0xD3,
    0xE5,
    0x80,  # CHIP_ID (the module's own radio ID)
    0x45,
    0x50,
    0x00,
    0x00,  # CHIP_VERSION
    *b"GATEWAYCTRL".ljust(16, b"\x00"),  # APP_DESCRIPTION, zero padded
]


def _response(code, data):
    """Parse a RESPONSE through the real ESP3 wire format, as the dongle would."""
    wire = Packet(PACKET.RESPONSE, data=[int(code), *data]).build()
    _status, _remaining, packet = Packet.parse_msg(bytearray(wire))
    assert isinstance(packet, ResponsePacket)
    return packet


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class CommonCommandCorrelationTests(unittest.TestCase):
    """Responses are attributed by write order; loss is reported as None."""

    def _written(self, communicator):
        packet = communicator.transmit.get_nowait()
        communicator._packet_written(packet)
        return packet

    def test_fifo_correlation_of_two_requests(self):
        communicator = Communicator()
        results = []
        self.assertTrue(
            communicator.request_common_command(
                [CO_RD_VERSION], lambda p: results.append(("version", p))
            )
        )
        self.assertTrue(
            communicator.request_common_command(
                [CO_RD_REPEATER], lambda p: results.append(("repeater", p))
            )
        )
        first = self._written(communicator)
        self.assertEqual(first.data, [CO_RD_VERSION])
        version = _response(RETURN_CODE.OK, VERSION_DATA)
        communicator._response_received(version)
        self.assertEqual(results, [("version", version)])
        second = self._written(communicator)
        self.assertEqual(second.data, [CO_RD_REPEATER])
        repeater = _response(RETURN_CODE.OK, [1, 2])
        communicator._response_received(repeater)
        self.assertEqual(results[1], ("repeater", repeater))
        # A response with nothing in flight reaches no handler.
        communicator._response_received(_response(RETURN_CODE.OK, []))
        self.assertEqual(len(results), 2)

    def test_timeout_and_stop_report_none(self):
        communicator = Communicator()
        results = []
        communicator.request_common_command([CO_RD_VERSION], results.append)
        communicator.request_common_command([CO_RD_REPEATER], results.append)
        written = self._written(communicator)
        communicator._response_timed_out(written)
        self.assertEqual(results, [None])
        communicator.stop()
        self.assertEqual(results, [None, None])
        self.assertIsNone(communicator._common_in_flight)
        self.assertFalse(communicator._common_requests)

    def test_base_id_and_common_commands_do_not_steal_each_other(self):
        communicator = Communicator()
        results = []
        self.assertTrue(communicator.request_base_id())
        communicator.request_common_command([CO_RD_VERSION], results.append)
        base_request = self._written(communicator)
        self.assertEqual(base_request.data, [0x08])
        communicator._response_received(
            _response(RETURN_CODE.OK, [0xFF, 0x87, 0xCA, 0x00])
        )
        self.assertEqual(communicator.current_base_id, [0xFF, 0x87, 0xCA, 0x00])
        self.assertEqual(results, [])
        self._written(communicator)
        communicator._response_received(_response(RETURN_CODE.OK, VERSION_DATA))
        self.assertEqual(len(results), 1)
        self.assertEqual(communicator.current_base_id, [0xFF, 0x87, 0xCA, 0x00])

    def test_a_failing_handler_never_kills_the_worker(self):
        communicator = Communicator()

        def explode(_packet):
            raise RuntimeError("handler bug")

        communicator.request_common_command([CO_RD_VERSION], explode)
        self._written(communicator)
        communicator._response_received(_response(RETURN_CODE.OK, VERSION_DATA))
        self.assertIsNone(communicator._common_in_flight)


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class ResponseParsingTests(unittest.TestCase):
    """ESP3 2.5.5 / 2.5.12 payloads decode strictly; anything else is None."""

    def test_version_response(self):
        info = parse_version_response(_response(RETURN_CODE.OK, VERSION_DATA))
        self.assertEqual(
            info,
            GatewayInfo(
                app_version="2.11.1.0",
                api_version="2.6.3.0",
                chip_id="FFD3E580",
                chip_version="69.80.0.0",
                app_description="GATEWAYCTRL",
            ),
        )
        self.assertIsNone(parse_version_response(_response(RETURN_CODE.ERROR, [])))
        self.assertIsNone(
            parse_version_response(_response(RETURN_CODE.OK, VERSION_DATA[:-1]))
        )
        self.assertIsNone(parse_version_response(None))

    def test_repeater_response(self):
        on = parse_repeater_response(_response(RETURN_CODE.OK, [1, 2]))
        self.assertEqual((on.enabled, on.level, on.raw_enable), (True, 2, 1))
        off = parse_repeater_response(_response(RETURN_CODE.OK, [0, 1]))
        self.assertEqual((off.enabled, off.level), (False, 1))
        selective = parse_repeater_response(_response(RETURN_CODE.OK, [2, 1]))
        self.assertEqual((selective.enabled, selective.raw_enable), (True, 2))
        for data in ([3, 1], [1, 7], [1], [], [1, 2, 3]):
            with self.subTest(data=data):
                self.assertIsNone(
                    parse_repeater_response(_response(RETURN_CODE.OK, data))
                )
        self.assertIsNone(
            parse_repeater_response(_response(RETURN_CODE.NOT_SUPPORTED, [1, 2]))
        )


if HA_AVAILABLE:

    class RecordingCommunicator(Communicator):
        """Real bookkeeping with the serial port and the thread stubbed out."""

        def __init__(
            self,
            port,
            callback=None,
            stopped_callback=None,
            written_callback=None,
            teach_in=False,
        ):
            super().__init__(callback, teach_in=teach_in)
            self.name = "EnOceanSerialCommunicator"

        def start(self):
            return None

        def is_alive(self):
            return True

        def close(self):
            return None


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DongleGatewayFactsTests(unittest.TestCase):
    """The dongle stores the read-backs and publishes them on the event loop."""

    def setUp(self):
        self.hass = MagicMock()
        with patch.object(dongle_module, "SerialCommunicator", RecordingCommunicator):
            self.dongle = EnOceanDongle(self.hass, "/dev/test-gateway", "entry-1")
        self.communicator = self.dongle._communicator
        self.dongle._available = True

    def _answer(self, code, data):
        packet = self.communicator.transmit.get_nowait()
        self.communicator._packet_written(packet)
        self.communicator._response_received(_response(code, data))
        return packet

    def test_version_and_repeater_are_read_without_any_radio(self):
        self.assertEqual(self.dongle.entry_id, "entry-1")
        self.assertTrue(self.dongle.request_version())
        self.assertTrue(self.dongle.request_repeater())
        version_request = self._answer(RETURN_CODE.OK, VERSION_DATA)
        self.assertEqual(version_request.packet_type, PACKET.COMMON_COMMAND)
        self.assertEqual(version_request.data, [CO_RD_VERSION])
        self.assertEqual(self.dongle.gateway_info.app_version, "2.11.1.0")
        repeater_request = self._answer(RETURN_CODE.OK, [0, 1])
        self.assertEqual(repeater_request.data, [CO_RD_REPEATER])
        self.assertFalse(self.dongle.repeater.enabled)
        # Both facts were published to the event loop, never read from a thread.
        signals = [
            call.args
            for call in self.hass.loop.call_soon_threadsafe.call_args_list
            if len(call.args) == 3 and call.args[2] == SIGNAL_GATEWAY_UPDATE
        ]
        self.assertEqual(len(signals), 2)

    def test_local_commands_build_packets_without_warnings(self):
        # 2.10.2: COMMON_COMMAND packets were built without ``optional`` and
        # the vendored Packet logged "Replacing Packet.optional with default
        # value." at WARNING level for every Base ID / version / repeater read.
        logger = "custom_components.enocean_custom.enocean_library.protocol.packet"
        with self.assertNoLogs(logger, level="WARNING"):
            self.assertTrue(self.dongle.request_version())
            self.assertTrue(self.dongle.request_repeater())

    def test_refusals_leave_the_facts_unknown(self):
        self.dongle.request_version()
        self._answer(RETURN_CODE.NOT_SUPPORTED, [])
        self.assertIsNone(self.dongle.gateway_info)
        self.dongle.request_repeater()
        self._answer(RETURN_CODE.OK, [9, 9])
        self.assertIsNone(self.dongle.repeater)

    def test_diagnostics_expose_versions_but_never_the_chip_id(self):
        self.dongle.request_version()
        self._answer(RETURN_CODE.OK, VERSION_DATA)
        diagnostics = self.dongle.diagnostics()
        self.assertEqual(
            diagnostics["gateway_info"],
            {
                "app_version": "2.11.1.0",
                "api_version": "2.6.3.0",
                "chip_version": "69.80.0.0",
                "app_description": "GATEWAYCTRL",
            },
        )
        self.assertNotIn("FFD3E580", repr(diagnostics))
        self.assertIsNone(diagnostics["repeater"])


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class GatewayDeviceAndEntityTests(unittest.IsolatedAsyncioTestCase):
    """The gateway device exists, radio devices hang off it, hub sensors read it."""

    async def asyncSetUp(self):
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            data={"device": "/dev/test-gateway"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=2,
            options={CONF_UI_DEVICES: []},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _gateway(self, **overrides):
        facts = {
            "entry_id": self.entry.entry_id,
            "gateway_info": None,
            "repeater": None,
            "available": True,
            "base_id": [0xFF, 0x87, 0xCA, 0x00],
            "received_radio_packets": 42,
            "last_packet_at": datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
        }
        facts.update(overrides)
        gateway = SimpleNamespace(**facts)
        self.entry.runtime_data = gateway
        return gateway

    async def test_gateway_device_is_created_then_enriched_by_the_version(self):
        gateway = self._gateway()
        device = async_register_gateway_device(self.hass, self.entry, gateway)
        identifier = (DOMAIN, gateway_device_identifier(self.entry.entry_id))
        self.assertIn(identifier, device.identifiers)
        self.assertEqual(device.manufacturer, "EnOcean")
        self.assertEqual(device.model, "ESP3 gateway")
        self.assertIsNone(device.sw_version)

        gateway.gateway_info = GatewayInfo(
            "2.11.1.0", "2.6.3.0", "FFD3E580", "69.80.0.0", "GATEWAYCTRL"
        )
        updated = async_register_gateway_device(self.hass, self.entry, gateway)
        self.assertEqual(updated.id, device.id)
        self.assertEqual(updated.sw_version, "2.11.1.0")
        self.assertEqual(updated.hw_version, "69.80.0.0")
        self.assertEqual(updated.model, "GATEWAYCTRL")
        self.assertEqual(updated.serial_number, "FFD3E580")

    async def test_radio_devices_reference_the_gateway_through_via_device(self):
        gateway = self._gateway()
        entity = EnOceanEntity([1, 2, 3, 4], "Rocker")
        entity.hass = self.hass
        identifier = (DOMAIN, gateway_device_identifier(self.entry.entry_id))
        if hasattr(dr, "async_get_device_id_by_identifier"):
            # HA >= 2026.10: the deprecated identifier tuple is never sent.
            gateway_device = async_register_gateway_device(
                self.hass, self.entry, gateway
            )
            info = entity.device_info
            self.assertNotIn("via_device", info)
            self.assertEqual(info["via_device_id"], gateway_device.id)
        else:
            info = entity.device_info
            self.assertNotIn("via_device_id", info)
            self.assertEqual(info["via_device"], identifier)
        # Without a loaded gateway nothing is invented.
        orphan = EnOceanEntity([1, 2, 3, 4], "Rocker")
        self.assertNotIn("via_device", orphan.device_info)
        self.assertNotIn("via_device_id", orphan.device_info)

    async def test_missing_gateway_device_invents_no_link(self):
        self._gateway()
        entity = EnOceanEntity([1, 2, 3, 4], "Rocker")
        entity.hass = self.hass
        if not hasattr(dr, "async_get_device_id_by_identifier"):
            self.skipTest("core predates via_device_id")
        info = entity.device_info
        self.assertNotIn("via_device", info)
        self.assertNotIn("via_device_id", info)

    async def test_hub_sensors_read_the_dongle_and_stay_available(self):
        gateway = self._gateway(available=False)
        sensors: list = []
        await sensor.async_setup_entry(self.hass, self.entry, sensors.extend)
        binaries: list = []
        await binary_sensor.async_setup_entry(self.hass, self.entry, binaries.extend)
        by_key = {entity.unique_id: entity for entity in (*sensors, *binaries)}
        entry_id = self.entry.entry_id
        counter = by_key[gateway_unique_id(entry_id, "gateway_received_telegrams")]
        last = by_key[gateway_unique_id(entry_id, "gateway_last_telegram")]
        base_id = by_key[gateway_unique_id(entry_id, "gateway_base_id")]
        connected = by_key[gateway_unique_id(entry_id, "gateway_connected")]
        self.assertEqual(counter.native_value, 42)
        self.assertEqual(last.native_value, datetime(2026, 10, 23, 12, 0, tzinfo=UTC))
        self.assertEqual(base_id.native_value, "FF:87:CA:00")
        self.assertFalse(base_id.entity_registry_enabled_default)
        self.assertFalse(connected.is_on)
        for entity in (counter, last, base_id, connected):
            self.assertTrue(entity.available, "hub facts stay readable")
            self.assertTrue(entity.has_entity_name)
            self.assertEqual(
                entity.device_info["identifiers"],
                {(DOMAIN, gateway_device_identifier(entry_id))},
            )
        gateway.base_id = None
        self.assertIsNone(base_id.native_value)

    async def test_platforms_without_a_real_entry_create_no_hub_entities(self):
        stub = SimpleNamespace(options={CONF_UI_DEVICES: []})
        sensors: list = []
        await sensor.async_setup_entry(self.hass, stub, sensors.extend)
        binaries: list = []
        await binary_sensor.async_setup_entry(self.hass, stub, binaries.extend)
        self.assertEqual(sensors, [])
        self.assertEqual(binaries, [])


if __name__ == "__main__":
    unittest.main()
