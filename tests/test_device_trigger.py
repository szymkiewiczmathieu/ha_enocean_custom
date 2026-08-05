"""Tests for native EnOcean rocker device triggers."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

try:
    import voluptuous as vol
    from homeassistant.components.device_automation import (
        InvalidDeviceAutomationConfig,
    )
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.const import (
        CONF_DEVICE_ID,
        CONF_DOMAIN,
        CONF_PLATFORM,
        CONF_TYPE,
    )
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import selector

    from custom_components.enocean_custom.binary_sensor import (
        A514_CONTACT_UNIQUE_ID_SUFFIX,
        EVENT_BUTTON_PRESSED,
        EnOceanBinarySensor,
    )
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.device_trigger import (
        TRIGGER_TYPES,
        async_attach_trigger,
        async_get_trigger_capabilities,
        async_get_triggers,
        async_validate_trigger_config,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        PACKET,
        PARSE_RESULT,
        RORG,
    )
    from custom_components.enocean_custom.enocean_library.protocol.packet import (
        Packet,
    )

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - bare unit-test environments
    HA_AVAILABLE = False

SENDER = [1, 2, 3, 4]
SENDER_HEX = "01020304"

# Status nibble 3 is a press, nibble 2 a release; anything else leaves the
# telegram kind undecided and binary_sensor reports ``pushed: None``.
STATUS_PRESSED = 0x30
STATUS_RELEASED = 0x20
STATUS_UNDECIDED = 0x00

# Rocker action byte to (which, onoff) per EnOceanBinarySensor.value_changed.
ACTION_CHANNEL_0 = 0x70
ACTION_CHANNEL_1 = 0x30


def _rps_packet(action: int, status: int):
    """Build and parse a real ESP3 RADIO_ERP1 RPS frame, as the dongle would.

    Going through the wire format is what proves the byte offsets the rocker
    decoder uses; a hand-built stub would agree with any indexing mistake.
    """
    wire = Packet(
        PACKET.RADIO_ERP1,
        data=[RORG.RPS, action, *SENDER, status],
        optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0x40, 0x00],
    ).build()
    result, remaining, packet = Packet.parse_msg(bytearray(wire))
    if result != PARSE_RESULT.OK or remaining:
        raise AssertionError("the test frame is not a valid ESP3 packet")
    return packet


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class DeviceTriggerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise discovery, validation and event matching with real HA registries."""

    async def asyncSetUp(self) -> None:
        """Create minimal Home Assistant registries."""
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        entry = ConfigEntry(
            data={"device": "/dev/test-device-trigger"},
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
        self._entry_id = entry.entry_id

    async def asyncTearDown(self) -> None:
        """Stop Home Assistant, after proving no trigger leaked a listener."""
        self.assertNotIn(EVENT_BUTTON_PRESSED, self.hass.bus.async_listeners())
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _register_device(
        self, sender: str = SENDER_HEX, *, domain: str | None = None
    ) -> str:
        """Register one device registry entry without any entity."""
        return (
            dr.async_get(self.hass)
            .async_get_or_create(
                config_entry_id=self._entry_id,
                identifiers={(domain or DOMAIN, sender)},
                name="Test device",
            )
            .id
        )

    def _device(
        self,
        sender: str = SENDER_HEX,
        *,
        domain: str | None = None,
        device_class: str | None = None,
        entity_domain: str = "binary_sensor",
        unique_id: str | None = None,
    ) -> str:
        """Register one device and its entity, as entity setup does."""
        device_id = self._register_device(sender, domain=domain)
        er.async_get(self.hass).async_get_or_create(
            entity_domain,
            domain or DOMAIN,
            unique_id if unique_id is not None else f"{sender}-{device_class}",
            device_id=device_id,
            original_device_class=device_class,
        )
        return device_id

    async def _attach(self, device_id: str, trigger_type: str, calls: list, **extra):
        """Attach one native trigger and return its removal callback."""

        async def action(variables, context=None) -> None:
            calls.append((variables, context))

        return await async_attach_trigger(
            self.hass,
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: trigger_type,
                **extra,
            },
            action,
            {"trigger_data": {}, "variables": {}},
        )

    async def _settle(self) -> None:
        """Drain the bus hops between a fired event and the trigger action.

        ``EventBus.fire`` schedules the dispatch threadsafe and the event
        trigger schedules the action with another ``call_soon``, so a single
        ``async_block_till_done`` returns before any task exists to wait for.
        """
        await self.hass.async_block_till_done()
        await self.hass.async_block_till_done()

    async def _fire(
        self, entity: EnOceanBinarySensor, action: int, status: int
    ) -> None:
        """Decode one real rocker telegram and let its event reach the bus."""
        entity.value_changed(_rps_packet(action, status))
        await self._settle()

    def _rocker_entity(self) -> EnOceanBinarySensor:
        """Build the rocker entity that actually emits button_pressed."""
        entity = EnOceanBinarySensor(SENDER, "Rocker", None)
        entity.hass = self.hass
        entity.schedule_update_ha_state = lambda: None  # type: ignore[method-assign]
        return entity

    async def test_get_triggers_for_rocker(self) -> None:
        """A rocker exposes exactly all four native trigger types."""
        device_id = self._device()
        triggers = await async_get_triggers(self.hass, device_id)
        self.assertEqual(
            {trigger[CONF_TYPE] for trigger in triggers}, set(TRIGGER_TYPES)
        )
        self.assertEqual(len(triggers), 4)
        for trigger in triggers:
            self.assertEqual(trigger[CONF_PLATFORM], "device")
            self.assertEqual(trigger[CONF_DOMAIN], DOMAIN)
            self.assertEqual(trigger[CONF_DEVICE_ID], device_id)

    async def test_get_triggers_is_awaitable(self) -> None:
        """device_automation gathers the result, so it must be a coroutine.

        A plain @callback returning a list makes asyncio.gather raise TypeError
        and takes down the whole "add device trigger" listing.
        """
        device_id = self._device()
        gathered = await asyncio.gather(
            *(async_get_triggers(self.hass, identifier) for identifier in (device_id,))
        )
        self.assertEqual(len(gathered[0]), 4)

    async def test_get_triggers_excludes_a51401_contact(self) -> None:
        """An A5-14-01 UI row creates only a door contact and has no triggers."""
        device_id = self._device(
            device_class="door",
            unique_id=f"16909060{A514_CONTACT_UNIQUE_ID_SUFFIX}",
        )
        self.assertEqual(await async_get_triggers(self.hass, device_id), [])

    async def test_get_triggers_for_every_rocker_device_class(self) -> None:
        """device_class is free-form user input and never gates the triggers."""
        for index, device_class in enumerate((None, "", "door", "opening")):
            with self.subTest(device_class=device_class):
                device_id = self._device(f"0102030{index}", device_class=device_class)
                self.assertEqual(len(await async_get_triggers(self.hass, device_id)), 4)

    async def test_get_triggers_without_binary_sensor(self) -> None:
        """A device that only owns a sensor entity exposes no rocker trigger."""
        device_id = self._device(entity_domain="sensor", device_class="temperature")
        self.assertEqual(await async_get_triggers(self.hass, device_id), [])
        self.assertEqual(
            await async_get_triggers(self.hass, self._register_device()), []
        )

    async def test_get_triggers_unknown_and_foreign_devices(self) -> None:
        """Unknown and other-domain devices return an empty list."""
        self.assertEqual(await async_get_triggers(self.hass, "missing"), [])
        foreign_id = self._device(domain="other_integration")
        self.assertEqual(await async_get_triggers(self.hass, foreign_id), [])

    async def test_malformed_identifiers_never_raise_and_never_attach(self) -> None:
        """A corrupt identifier hides the triggers instead of crashing the UI."""
        for sender in ("0102030", "010203040", "0102030G", " 1020304", "0x102030"):
            with self.subTest(sender=sender):
                device_id = self._device(sender)
                self.assertEqual(await async_get_triggers(self.hass, device_id), [])
                with self.assertRaises(InvalidDeviceAutomationConfig):
                    await self._attach(device_id, "button_pressed", [])

    async def test_validate_trigger_config(self) -> None:
        """Only the four public trigger types on a real device validate."""
        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: self._device(),
            CONF_TYPE: "button_pressed",
        }
        self.assertEqual(await async_validate_trigger_config(self.hass, config), config)
        with self.assertRaises(vol.Invalid):
            await async_validate_trigger_config(
                self.hass, {**config, CONF_TYPE: "unknown"}
            )
        with self.assertRaises(InvalidDeviceAutomationConfig):
            await async_validate_trigger_config(
                self.hass, {**config, CONF_DEVICE_ID: "missing"}
            )

    async def test_trigger_capabilities(self) -> None:
        """Both optional filters use closed HA select lists."""
        capabilities = await async_get_trigger_capabilities(self.hass, {})
        schema = capabilities["extra_fields"]
        self.assertIsInstance(schema, vol.Schema)
        fields = {
            marker.schema: validator for marker, validator in schema.schema.items()
        }
        self.assertEqual(set(fields), {"which", "onoff"})
        self.assertTrue(all(isinstance(key, vol.Optional) for key in schema.schema))
        self.assertEqual(fields["which"].config["options"], ["0", "1", "10"])
        self.assertEqual(fields["onoff"].config["options"], ["0", "1"])
        self.assertFalse(fields["which"].config["custom_value"])
        self.assertFalse(fields["onoff"].config["custom_value"])
        self.assertIsInstance(fields["which"], selector.SelectSelector)

    async def test_validate_optional_filters_and_channel_conflicts(self) -> None:
        """Only decoded values validate and fixed channel types cannot conflict."""
        base = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: self._device(),
            CONF_TYPE: "button_pressed",
        }
        validated = await async_validate_trigger_config(
            self.hass, {**base, "which": "10", "onoff": "0"}
        )
        self.assertEqual((validated["which"], validated["onoff"]), (10, 0))

        for key, value in (
            ("which", 2),
            ("which", "other"),
            ("which", 1.0),
            ("which", "01"),
            ("which", " 1"),
            ("which", "+1"),
            ("onoff", 2),
            ("onoff", "other"),
            ("onoff", True),
            ("onoff", "01"),
            ("onoff", " 1"),
            ("onoff", "+1"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(vol.Invalid):
                await async_validate_trigger_config(self.hass, {**base, key: value})

        for trigger_type, which in (
            ("button_pressed_channel_0", 1),
            ("button_pressed_channel_0", 10),
            ("button_pressed_channel_1", 0),
            ("button_pressed_channel_1", 10),
        ):
            with (
                self.subTest(trigger_type=trigger_type, which=which),
                self.assertRaisesRegex(vol.Invalid, "requires which="),
            ):
                await async_validate_trigger_config(
                    self.hass,
                    {**base, CONF_TYPE: trigger_type, "which": which},
                )

        # Redundant but consistent: a which that already matches the fixed
        # channel (or the generic button_pressed type, which has none) validates.
        for trigger_type, which in (
            ("button_pressed_channel_0", 0),
            ("button_pressed", 0),
        ):
            with self.subTest(trigger_type=trigger_type, which=which):
                validated = await async_validate_trigger_config(
                    self.hass, {**base, CONF_TYPE: trigger_type, "which": which}
                )
                self.assertEqual(validated["which"], which)

    async def test_validate_trigger_config_is_idempotent(self) -> None:
        """Re-validating an already-normalized config does not change it.

        The UI editor's save endpoint persists the raw frontend payload as-is
        and relies on the automation reload path to re-validate it, so a second
        pass through async_validate_trigger_config must be a no-op, not a
        second destructive string-to-int conversion (which would already fail
        since the value is an int, not the numeric string it once was).
        """
        base = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: self._device(),
            CONF_TYPE: "button_pressed",
            "which": "10",
            "onoff": "0",
        }
        once = await async_validate_trigger_config(self.hass, base)
        twice = await async_validate_trigger_config(self.hass, once)
        self.assertEqual(once, twice)
        self.assertEqual((twice["which"], twice["onoff"]), (10, 0))

    async def test_attach_trigger_normalizes_unvalidated_string_filters(self) -> None:
        """async_attach_trigger defends against a config that skipped validation.

        Home Assistant always calls async_validate_trigger_config before
        attaching (setup, reload after a UI save, and the live "test trigger"
        websocket preview all do), which is what turns the numeric strings a
        SelectSelector stores into the ints the button_pressed event carries.
        This proves the belt-and-suspenders case: even a caller that attaches
        straight from raw SelectSelector strings still matches the int event,
        instead of silently never firing.
        """
        device_id = self._device()
        calls: list = []
        remove = await self._attach(
            device_id, "button_released", calls, which="1", onoff="0"
        )
        try:
            self.hass.bus.async_fire(
                EVENT_BUTTON_PRESSED,
                {"id": SENDER, "pushed": 0, "which": 1, "onoff": 0},
            )
            await self._settle()
            self.assertEqual(len(calls), 1)
        finally:
            remove()

    async def test_optional_filters_match_exact_event_subset(self) -> None:
        """Configured which/onoff values participate in event-data matching."""
        device_id = self._device()
        calls: list = []
        remove = await self._attach(
            device_id, "button_released", calls, which=10, onoff=1
        )
        try:
            exact = {"id": SENDER, "pushed": 0, "which": 10, "onoff": 1}
            self.hass.bus.async_fire(EVENT_BUTTON_PRESSED, exact)
            await self._settle()
            self.assertEqual(len(calls), 1)

            self.hass.bus.async_fire(EVENT_BUTTON_PRESSED, {**exact, "onoff": 0})
            await self._settle()
            self.assertEqual(len(calls), 1)
        finally:
            remove()

    async def test_omitted_optional_filters_preserve_broad_matching(self) -> None:
        """A v2.4.0 trigger still ignores which and onoff."""
        device_id = self._device()
        calls: list = []
        remove = await self._attach(device_id, "button_released", calls)
        try:
            for which, onoff in ((0, 0), (10, 1)):
                self.hass.bus.async_fire(
                    EVENT_BUTTON_PRESSED,
                    {"id": SENDER, "pushed": 0, "which": which, "onoff": onoff},
                )
                await self._settle()
            self.assertEqual(len(calls), 2)
        finally:
            remove()

    async def test_trigger_never_matches_another_sender(self) -> None:
        """The radio id of the configured device is part of the match."""
        device_id = self._device()
        calls: list = []
        remove = await self._attach(device_id, "button_pressed", calls)
        try:
            self.hass.bus.async_fire(
                EVENT_BUTTON_PRESSED,
                {
                    "name": "Other",
                    "id": [9, 9, 9, 9],
                    "pushed": 1,
                    "which": 0,
                    "onoff": 0,
                    "repeated_telegram": 0,
                },
            )
            await self._settle()
            self.assertEqual(calls, [])
        finally:
            remove()

    async def test_press_and_release_are_mutually_exclusive(self) -> None:
        """A real press fires only the press trigger, a release only the other."""
        device_id = self._device()
        entity = self._rocker_entity()
        pressed: list = []
        released: list = []
        removals = [
            await self._attach(device_id, "button_pressed", pressed),
            await self._attach(device_id, "button_released", released),
        ]
        try:
            await self._fire(entity, ACTION_CHANNEL_0, STATUS_PRESSED)
            self.assertEqual((len(pressed), len(released)), (1, 0))

            await self._fire(entity, ACTION_CHANNEL_0, STATUS_RELEASED)
            self.assertEqual((len(pressed), len(released)), (1, 1))
        finally:
            for remove in removals:
                remove()

    async def test_undecided_telegram_fires_nothing(self) -> None:
        """``pushed: None`` matches neither 1 nor 0, so no trigger runs."""
        device_id = self._device()
        entity = self._rocker_entity()
        calls: list = []
        removals = [
            await self._attach(device_id, trigger_type, calls)
            for trigger_type in sorted(TRIGGER_TYPES)
        ]
        try:
            events: list = []
            removals.append(
                self.hass.bus.async_listen(EVENT_BUTTON_PRESSED, events.append)
            )
            await self._fire(entity, ACTION_CHANNEL_0, STATUS_UNDECIDED)
            self.assertEqual([event.data["pushed"] for event in events], [None])
            self.assertEqual(calls, [])
        finally:
            for remove in removals:
                remove()

    async def test_channel_triggers_select_one_rocker_half(self) -> None:
        """Channel triggers add ``which`` to the match without losing the rest."""
        device_id = self._device()
        entity = self._rocker_entity()
        channel_0: list = []
        channel_1: list = []
        removals = [
            await self._attach(device_id, "button_pressed_channel_0", channel_0),
            await self._attach(device_id, "button_pressed_channel_1", channel_1),
        ]
        try:
            await self._fire(entity, ACTION_CHANNEL_0, STATUS_PRESSED)
            self.assertEqual((len(channel_0), len(channel_1)), (1, 0))

            await self._fire(entity, ACTION_CHANNEL_1, STATUS_PRESSED)
            self.assertEqual((len(channel_0), len(channel_1)), (1, 1))

            # A release on channel 0 must not revive the press trigger.
            await self._fire(entity, ACTION_CHANNEL_0, STATUS_RELEASED)
            self.assertEqual((len(channel_0), len(channel_1)), (1, 1))
        finally:
            for remove in removals:
                remove()


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class TriggerTranslationTests(unittest.TestCase):
    """Keep the device_automation strings in step with the exposed types."""

    _COMPONENT = (
        Path(__file__).resolve().parents[1] / "custom_components/enocean_custom"
    )
    _FILES = (
        "strings.json",
        "translations/en.json",
        "translations/fr.json",
    )

    def _trigger_type_strings(self, name: str) -> dict[str, str]:
        """Load one localisation file's device_automation trigger names."""
        payload = json.loads((self._COMPONENT / name).read_text(encoding="utf-8"))
        return payload["device_automation"]["trigger_type"]

    def test_every_trigger_type_is_named_in_every_language(self) -> None:
        """Home Assistant renders trigger_type[<type>], so all four must exist."""
        for name in self._FILES:
            with self.subTest(file=name):
                strings = self._trigger_type_strings(name)
                self.assertEqual(set(strings), set(TRIGGER_TYPES))
                for label in strings.values():
                    self.assertTrue(label.strip())

    def test_strings_and_english_translations_agree(self) -> None:
        """en.json is the shipped copy of strings.json and must not drift."""
        self.assertEqual(
            self._trigger_type_strings("strings.json"),
            self._trigger_type_strings("translations/en.json"),
        )


if __name__ == "__main__":
    unittest.main()
