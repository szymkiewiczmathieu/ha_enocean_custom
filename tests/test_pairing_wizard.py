"""Tests for the guided actuator pairing options flow."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

try:  # The bare CI lifecycle environment has no Home Assistant installed.
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.const import CONF_ENTITY_ID
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.enocean_custom import options_flow
    from custom_components.enocean_custom.const import (
        DOMAIN,
        SERVICE_SEND_TEACH_IN,
        SIGNAL_RECEIVE_MESSAGE,
    )
    from custom_components.enocean_custom.enocean_library.protocol.constants import (
        RORG,
    )
    from custom_components.enocean_custom.schema import CONF_UI_DEVICES

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant not installed")
class PairingWizardTests(unittest.IsolatedAsyncioTestCase):
    """Exercise persistence, progress, outcomes, and cleanup."""

    async def asyncSetUp(self) -> None:
        """Create the minimal registries and services used by an options flow."""
        self._config_dir = TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        await ar.async_load(self.hass, load_empty=True)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.toggle_calls: list[str] = []
        self.teach_in_calls: list[str] = []

        async def _toggle(call) -> None:
            self.toggle_calls.append(call.data[CONF_ENTITY_ID])

        async def _send_teach_in(call) -> None:
            self.teach_in_calls.append(call.data[CONF_ENTITY_ID])

        self.hass.services.async_register("switch", "toggle", _toggle)
        self.hass.services.async_register(DOMAIN, SERVICE_SEND_TEACH_IN, _send_teach_in)

    async def asyncTearDown(self) -> None:
        """Stop Home Assistant and discard its temporary config directory."""
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _new_entry(self) -> ConfigEntry:
        """Add and return a minimal config entry."""
        entry = ConfigEntry(
            data={"device": "/dev/test-pairing"},
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
        return entry

    def _bind_flow(self, entry: ConfigEntry):
        """Bind a real options flow without the flow manager."""
        flow = options_flow.EnOceanOptionsFlow()
        flow.hass = self.hass
        flow.handler = entry.entry_id
        return flow

    async def _create_actuator(
        self,
        *,
        actuator_id: str,
        actuator_type: str,
        sender_id: str | None = None,
    ):
        """Drive identification through persistence and register its entity."""
        entry = self._new_entry()
        flow = self._bind_flow(entry)
        type_step = await flow.async_step_pair_actuator({"qr_code": actuator_id})
        self.assertEqual(type_step["step_id"], "pair_actuator_type")
        details_step = await flow.async_step_pair_actuator_type(
            {"name": f"Actuator {actuator_id}", "actuator_type": actuator_type}
        )
        self.assertEqual(details_step["step_id"], "pair_actuator_details")
        details = {"channel": 0}
        if sender_id is not None:
            details["sender_id"] = sender_id
        instructions = await flow.async_step_pair_actuator_details(details)
        self.assertEqual(instructions["step_id"], "pair_actuator_instructions")
        self.assertEqual(len(entry.options[CONF_UI_DEVICES]), 1)

        device = entry.options[CONF_UI_DEVICES][0]
        registry_entry = er.async_get(self.hass).async_get_or_create(
            device["platform"],
            DOMAIN,
            options_flow._unique_id_for(device),
        )
        return flow, entry, registry_entry.entity_id

    @staticmethod
    def _d2_status(sender_id: str, channel: int = 0):
        """Build a valid synthetic D2-01 actuator status response."""
        sender = [int(part, 16) for part in sender_id.split(":")]
        return SimpleNamespace(
            sender_int=int(sender_id.replace(":", ""), 16),
            data=[RORG.VLD, 0x04, channel, 50, *sender, 0x00],
        )

    async def test_relay_creation_toggle_d2_status_and_success(self) -> None:
        """A valid module status confirms the complete relay flow."""
        actuator_id = "11:22:33:44"
        flow, _entry, entity_id = await self._create_actuator(
            actuator_id=actuator_id,
            actuator_type="relay_rps",
        )
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 0.5),
            patch.object(options_flow, "PAIRING_RELAY_INTERVAL", 0.01),
        ):
            progress = await flow.async_step_pair_actuator_instructions({})
            self.assertEqual(progress["type"], FlowResultType.SHOW_PROGRESS)
            while not self.toggle_calls:
                await asyncio.sleep(0)
            self.assertEqual(self.toggle_calls, [entity_id])
            async_dispatcher_send(
                self.hass,
                SIGNAL_RECEIVE_MESSAGE,
                SimpleNamespace(sender_int=0x11223344, data=None),
            )
            async_dispatcher_send(
                self.hass,
                SIGNAL_RECEIVE_MESSAGE,
                self._d2_status("AA:BB:CC:DD"),
            )
            self.assertFalse(flow._pairing_task.done())
            async_dispatcher_send(
                self.hass,
                SIGNAL_RECEIVE_MESSAGE,
                self._d2_status(actuator_id),
            )
            await asyncio.wait_for(flow._pairing_task, 1)
            calls_at_success = len(self.toggle_calls)
            await asyncio.sleep(0.03)
            self.assertEqual(len(self.toggle_calls), calls_at_success)

        done = await flow.async_step_pair_actuator_progress()
        self.assertEqual(done["type"], FlowResultType.SHOW_PROGRESS_DONE)
        self.assertEqual(done["step_id"], "pair_relay_success")
        success = await flow.async_step_pair_relay_success(None)
        self.assertEqual(success["type"], FlowResultType.FORM)
        finished = await flow.async_step_pair_relay_success({})
        self.assertEqual(finished["type"], FlowResultType.CREATE_ENTRY)

    async def test_timeout_retry_keep_and_delete(self) -> None:
        """All three timeout outcomes preserve their documented semantics."""
        for offset, action in enumerate(("retry", "keep", "delete"), start=1):
            with self.subTest(action=action):
                actuator_id = f"21:22:33:{offset:02X}"
                flow, entry, entity_id = await self._create_actuator(
                    actuator_id=actuator_id,
                    actuator_type="relay_rps",
                )
                before = len(self.toggle_calls)
                with (
                    patch.object(options_flow, "PAIRING_TIMEOUT", 0.03),
                    patch.object(options_flow, "PAIRING_RELAY_INTERVAL", 0.005),
                ):
                    await flow.async_step_pair_actuator_instructions({})
                    await asyncio.wait_for(flow._pairing_task, 1)
                    calls_at_timeout = len(self.toggle_calls)
                    await asyncio.sleep(0.02)
                    self.assertGreater(calls_at_timeout, before)
                    self.assertEqual(len(self.toggle_calls), calls_at_timeout)

                done = await flow.async_step_pair_actuator_progress()
                self.assertEqual(done["step_id"], "pair_actuator_failure")
                failure = await flow.async_step_pair_actuator_failure(None)
                self.assertEqual(failure["type"], FlowResultType.FORM)
                result = await flow.async_step_pair_actuator_failure(
                    {"failure_action": action}
                )
                if action == "retry":
                    self.assertEqual(result["step_id"], "pair_actuator_instructions")
                    self.assertIsNone(flow._pairing_task)
                    self.assertEqual(len(entry.options[CONF_UI_DEVICES]), 1)
                elif action == "keep":
                    self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
                    self.assertEqual(len(entry.options[CONF_UI_DEVICES]), 1)
                else:
                    self.assertEqual(result["type"], FlowResultType.CREATE_ENTRY)
                    self.assertEqual(entry.options[CONF_UI_DEVICES], [])
                    self.assertIsNone(er.async_get(self.hass).async_get(entity_id))

    async def test_dimmer_sends_existing_service_then_reports_honest_success(
        self,
    ) -> None:
        """The 4BS flow calls send_teach_in N times and claims no feedback."""
        flow, _entry, entity_id = await self._create_actuator(
            actuator_id="31:32:33:34",
            actuator_type="dimmer_4bs",
            sender_id="05:9F:89:34",
        )
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 0.5),
            patch.object(options_flow, "PAIRING_DIMMER_INTERVAL", 0.005),
            patch.object(options_flow, "PAIRING_DIMMER_ATTEMPTS", 3),
        ):
            await flow.async_step_pair_actuator_instructions({})
            await asyncio.wait_for(flow._pairing_task, 1)
        self.assertEqual(self.teach_in_calls, [entity_id, entity_id, entity_id])
        done = await flow.async_step_pair_actuator_progress()
        self.assertEqual(done["step_id"], "pair_dimmer_success")
        success = await flow.async_step_pair_dimmer_success(None)
        self.assertEqual(success["type"], FlowResultType.FORM)

    async def test_abandon_cancels_loop_without_zombie_commands(self) -> None:
        """Discarding the flow stops its background task and future toggles."""
        flow, _entry, _entity_id = await self._create_actuator(
            actuator_id="41:42:43:44",
            actuator_type="relay_rps",
        )
        with (
            patch.object(options_flow, "PAIRING_TIMEOUT", 1),
            patch.object(options_flow, "PAIRING_RELAY_INTERVAL", 0.005),
        ):
            await flow.async_step_pair_actuator_instructions({})
            while not self.toggle_calls:
                await asyncio.sleep(0)
            task = flow._pairing_task
            flow.async_remove()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            calls_at_remove = len(self.toggle_calls)
            await asyncio.sleep(0.02)
        self.assertTrue(task.cancelled())
        self.assertEqual(len(self.toggle_calls), calls_at_remove)


if __name__ == "__main__":
    unittest.main()
