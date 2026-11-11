"""The event-loop guard itself, and a static ratchet on blocking primitives.

Importing this module installs the process-wide guard of
``tests/event_loop_guard.py``: during ``python -m unittest discover -s tests``
every test of the suite then runs under it. A violation raises at the call
site; ``tests/test_zz_event_loop_verdict.py`` reports the ones a broad
``except`` swallowed.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from tests.event_loop_guard import (
    INTEGRATION_DIR,
    SUITE_GUARD,
    EventLoopGuard,
    forbid_blocking_calls_in_event_loop,
)

try:  # The bare unit-test environment has no Home Assistant installed.
    import serial
    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers import issue_registry as ir

    import custom_components.enocean_custom as integration
    from custom_components.enocean_custom import eep_catalog
    from custom_components.enocean_custom.const import DOMAIN
    from custom_components.enocean_custom.schema import valid_ui_devices

    HA_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised by bare CI env
    HA_AVAILABLE = False

SUITE_GUARD.install()

INTEGRATION = ROOT / "custom_components" / "enocean_custom"

# Source compiled under a file name inside the integration directory: the
# guard blames by file name, so these functions stand in for integration code
# without adding a deliberately broken module to the package.
_FAKE_SOURCE = """
import asyncio
from pathlib import Path


def plain_helper(path):
    return Path(path).read_text()


async def async_reads_on_the_loop(path):
    return plain_helper(path)


def async_callback_reads(path):
    return plain_helper(path)


async def async_reads_in_the_executor(path):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, plain_helper, path)


async def async_sleeps_on_the_loop():
    import time

    time.sleep(0)


async def async_swallows(path):
    try:
        return plain_helper(path)
    except Exception:
        return "swallowed"
"""


def _fake_integration_module() -> dict:
    namespace: dict = {}
    code = compile(_FAKE_SOURCE, INTEGRATION_DIR + "fake_blocking_module.py", "exec")
    exec(code, namespace)  # noqa: S102 - fixed source above, compiled for its file name
    return namespace


class EventLoopGuardTests(unittest.IsolatedAsyncioTestCase):
    """Prove what the guard reports and what it leaves alone."""

    def setUp(self):
        self.fake = _fake_integration_module()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("payload")
            self.path = handle.name
        # Run each case under its own guard; the suite guard is put back after.
        self._suite_was_installed = SUITE_GUARD.installed
        SUITE_GUARD.uninstall()

    def tearDown(self):
        Path(self.path).unlink()
        if self._suite_was_installed:
            SUITE_GUARD.install()

    async def test_file_read_in_an_integration_coroutine_is_reported(self):
        with (
            forbid_blocking_calls_in_event_loop() as guard,
            self.assertRaises(AssertionError) as raised,
        ):
            await self.fake["async_reads_on_the_loop"](self.path)
        self.assertEqual(len(guard.violations), 1)
        violation = guard.violations[0]
        self.assertEqual(violation.call, "Path.read_text")
        self.assertIn("plain_helper", violation.location)
        self.assertIn("async_reads_on_the_loop", violation.entry_point)
        self.assertIn("ran inside the event loop", str(raised.exception))

    async def test_loop_only_function_called_synchronously_is_reported(self):
        with (
            forbid_blocking_calls_in_event_loop() as guard,
            self.assertRaises(AssertionError),
        ):
            self.fake["async_callback_reads"](self.path)
        self.assertEqual([v.call for v in guard.violations], ["Path.read_text"])

    async def test_callback_scheduled_on_the_loop_is_reported(self):
        loop = asyncio.get_running_loop()
        errors: list[BaseException] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: errors.append(context.get("exception"))
        )
        try:
            with forbid_blocking_calls_in_event_loop() as guard:
                loop.call_soon(self.fake["plain_helper"], self.path)
                await asyncio.sleep(0.01)
        finally:
            loop.set_exception_handler(previous)
        self.assertEqual([v.call for v in guard.violations], ["Path.read_text"])
        self.assertTrue(errors and isinstance(errors[0], AssertionError))

    async def test_blocking_sleep_is_reported(self):
        with (
            forbid_blocking_calls_in_event_loop() as guard,
            self.assertRaises(AssertionError),
        ):
            await self.fake["async_sleeps_on_the_loop"]()
        self.assertEqual([v.call for v in guard.violations], ["time.sleep"])

    async def test_swallowed_violation_is_still_recorded(self):
        with forbid_blocking_calls_in_event_loop() as guard:
            result = await self.fake["async_swallows"](self.path)
        self.assertEqual(result, "swallowed")
        self.assertEqual(len(guard.violations), 1)
        self.assertIn("async_swallows", guard.describe())

    async def test_executor_job_is_not_reported(self):
        with forbid_blocking_calls_in_event_loop() as guard:
            result = await self.fake["async_reads_in_the_executor"](self.path)
        self.assertEqual(result, "payload")
        self.assertEqual(guard.violations, [])

    async def test_plain_helper_driven_by_a_test_is_not_reported(self):
        # Production runs such helpers in the executor; a test calling one
        # from ``async def`` is the test's own choice.
        with forbid_blocking_calls_in_event_loop() as guard:
            self.assertEqual(self.fake["plain_helper"](self.path), "payload")
        self.assertEqual(guard.violations, [])

    async def test_code_outside_the_integration_is_not_reported(self):
        with forbid_blocking_calls_in_event_loop() as guard:
            self.assertEqual(Path(self.path).read_text(), "payload")
        self.assertEqual(guard.violations, [])

    def test_no_event_loop_means_no_report(self):
        with forbid_blocking_calls_in_event_loop() as guard:
            self.assertEqual(self.fake["async_callback_reads"](self.path), "payload")
        self.assertEqual(guard.violations, [])

    def test_uninstall_restores_every_primitive(self):
        import builtins
        import time

        before = (builtins.open, Path.read_text, time.sleep)
        guard = EventLoopGuard().install()
        self.assertTrue(guard.installed)
        self.assertIsNot(builtins.open, before[0])
        guard.install()  # idempotent
        guard.uninstall()
        self.assertFalse(guard.installed)
        self.assertEqual((builtins.open, Path.read_text, time.sleep), before)


GENERIC_ROW = {
    "id": [0x01, 0x02, 0x03, 0x04],
    "platform": "sensor",
    "name": "Room",
    "device_class": "eep",
    "radio_metadata": {"eep": "A5-04-01"},
}


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant is not installed")
class ColdStartTests(unittest.IsolatedAsyncioTestCase):
    """The profile catalog is warm before anything validates a row on the loop.

    Validating a generic ``eep`` sensor row needs the catalog, which is read
    from disk. The options migration, the options flow and the admin panel
    validate rows on the event loop, including while the entry is retrying a
    dongle that could not be opened: the catalog must already be built then.
    """

    async def asyncSetUp(self):
        self._config_dir = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self._config_dir.name)
        loader.async_setup(self.hass)
        await ir.async_load(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self._suite_was_installed = SUITE_GUARD.installed
        SUITE_GUARD.uninstall()
        eep_catalog.built_in_profiles.cache_clear()

    async def asyncTearDown(self):
        eep_catalog.built_in_profiles.cache_clear()
        if self._suite_was_installed:
            SUITE_GUARD.install()
        await self.hass.async_stop(force=True)
        self._config_dir.cleanup()

    def _entry(self) -> ConfigEntry:
        entry = ConfigEntry(
            data={"device": "/dev/test"},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=3,
            options={"ui_devices": [dict(GENERIC_ROW)]},
            source="user",
            subentries_data=None,
            title="EnOcean",
            unique_id=None,
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        return entry

    async def _validate_rows_from_a_loop_callback(self) -> list[BaseException]:
        """Validate a generic row straight from the loop, as a dispatcher does.

        No test frame sits between the event loop and the integration, so the
        guard judges the call exactly as it would in production. Returns the
        exceptions the callback raised.
        """
        loop = asyncio.get_running_loop()
        errors: list[BaseException] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: errors.append(context.get("exception"))
        )
        try:
            loop.call_soon(functools.partial(valid_ui_devices, [dict(GENERIC_ROW)]))
            await asyncio.sleep(0.01)
        finally:
            loop.set_exception_handler(previous)
        return errors

    async def test_a_cold_catalog_read_on_the_loop_is_what_the_guard_reports(self):
        with forbid_blocking_calls_in_event_loop() as guard:
            errors = await self._validate_rows_from_a_loop_callback()
        self.assertEqual([v.call for v in guard.violations], ["Path.read_text"])
        self.assertIn("_supplemental_profiles", guard.violations[0].location)
        self.assertIn("valid_ui_devices", guard.violations[0].entry_point)
        self.assertTrue(errors and isinstance(errors[0], AssertionError))

    async def test_integration_setup_warms_the_catalog_in_the_executor(self):
        async def no_panel(hass):
            return None

        with (
            mock.patch.object(integration, "async_setup_panel", no_panel),
            forbid_blocking_calls_in_event_loop() as guard,
        ):
            self.assertTrue(await integration.async_setup(self.hass, {}))
            self.assertEqual(eep_catalog.built_in_profiles.cache_info().currsize, 1)
            errors = await self._validate_rows_from_a_loop_callback()
        self.assertEqual(errors, [])
        self.assertEqual(guard.violations, [])

    async def test_a_dongle_that_cannot_be_opened_still_leaves_the_catalogs_ready(
        self,
    ):
        entry = self._entry()
        loaded: list[bool] = []

        def unplugged(hass, serial_path, entry_id=None):
            raise serial.SerialException("could not open port")

        real_load = integration.async_load_user_profiles

        async def load_user_profiles(hass):
            loaded.append(True)
            return await real_load(hass)

        with (
            mock.patch.object(integration, "EnOceanDongle", unplugged),
            mock.patch.object(
                integration, "async_load_user_profiles", load_user_profiles
            ),
            forbid_blocking_calls_in_event_loop() as guard,
        ):
            with self.assertRaises(ConfigEntryNotReady):
                await integration.async_setup_entry(self.hass, entry)
            self.assertEqual(eep_catalog.built_in_profiles.cache_info().currsize, 1)
            errors = await self._validate_rows_from_a_loop_callback()
        self.assertEqual(loaded, [True])
        self.assertEqual(errors, [])
        self.assertEqual(guard.violations, [])


# --- Static ratchet -------------------------------------------------------
#
# Every call to a blocking primitive in the maintained integration code is
# listed here with the reason it never runs on the event loop. A new call
# site fails ``test_every_blocking_call_site_is_accounted_for`` until it is
# added with its own justification (or, better, moved to the executor).

_BLOCKING_NAMES = frozenset({"open"})
_BLOCKING_ATTRIBUTES = frozenset(
    {
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "glob",
        "iglob",
        "listdir",
        "scandir",
        "walk",
        "samefile",
        "serial_for_url",
        "sleep",
    }
)
_BLOCKING_DOTTED = frozenset({"os.path.exists", "os.path.isfile", "os.path.isdir"})
# Constructing the serial worker opens the port (``serial_for_url``).
_BLOCKING_CONSTRUCTORS = frozenset({"SerialCommunicator"})

ALLOWED_BLOCKING_CALL_SITES = {
    # Module import: Home Assistant imports integrations in its import executor.
    ("device_catalog.py", "_load_product_recipes", "read_text"),
    ("device_intelligence.py", "_load_ddf_catalog", "read_text"),
    # Run by async_warm_built_in_profiles through hass.async_add_executor_job.
    ("eep_catalog.py", "_supplemental_profiles", "read_text"),
    # Run by async_load_user_profiles through hass.async_add_executor_job.
    ("user_profiles.py", "_read_file", "read_text"),
    # EnOceanDongle is constructed by async_setup_entry in the executor.
    ("dongle.py", "EnOceanDongle.__init__", "SerialCommunicator"),
    # Run by _attempt_reconnect through hass.async_add_executor_job.
    ("dongle.py", "EnOceanDongle._build_communicator", "os.path.exists"),
    ("dongle.py", "EnOceanDongle._build_communicator", "SerialCommunicator"),
    # Called by the config flow through hass.async_add_executor_job.
    ("dongle.py", "detect", "glob"),
    ("dongle.py", "same_device", "samefile"),
    ("dongle.py", "validate_path", "SerialCommunicator"),
}


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _blocking_primitive(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        if function.id in _BLOCKING_NAMES or function.id in _BLOCKING_CONSTRUCTORS:
            return function.id
        if function.id in ("samefile", "glob", "sleep"):
            return function.id
        return None
    if isinstance(function, ast.Attribute):
        dotted = _dotted(function)
        if dotted in _BLOCKING_DOTTED:
            return dotted
        if function.attr in _BLOCKING_ATTRIBUTES:
            # ``asyncio.sleep`` is the non-blocking one.
            if function.attr == "sleep" and dotted == "asyncio.sleep":
                return None
            return function.attr
        if function.attr in _BLOCKING_CONSTRUCTORS:
            return function.attr
    return None


def blocking_call_sites(source: str) -> set[tuple[str, str]]:
    """Return ``(qualified function, primitive)`` for every blocking call."""
    sites: set[tuple[str, str]] = set()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Call):
                primitive = _blocking_primitive(child)
                if primitive is not None:
                    sites.add((".".join(scope) or "<module>", primitive))
            visit(child, scope)

    visit(ast.parse(source), ())
    return sites


class BlockingCallSiteRatchetTests(unittest.TestCase):
    """No blocking primitive may appear outside the audited call sites."""

    def _found(self) -> set[tuple[str, str, str]]:
        found: set[tuple[str, str, str]] = set()
        for path in sorted(INTEGRATION.glob("*.py")):
            for function, primitive in blocking_call_sites(path.read_text("utf-8")):
                found.add((path.name, function, primitive))
        return found

    def test_every_blocking_call_site_is_accounted_for(self):
        unexpected = self._found() - ALLOWED_BLOCKING_CALL_SITES
        self.assertEqual(
            unexpected,
            set(),
            "new blocking call site(s) in the integration: run them in the "
            "executor, then list them in ALLOWED_BLOCKING_CALL_SITES with the "
            "reason they never run on the event loop",
        )

    def test_the_audited_list_has_no_stale_entry(self):
        self.assertEqual(ALLOWED_BLOCKING_CALL_SITES - self._found(), set())

    def test_the_scanner_sees_the_primitives_it_is_meant_to(self):
        source = (
            "import asyncio, glob, os, time\n"
            "from pathlib import Path\n"
            "async def async_bad(hass, path):\n"
            "    Path(path).read_text()\n"
            "    open(path)\n"
            "    glob.glob('*')\n"
            "    os.path.exists(path)\n"
            "    time.sleep(1)\n"
            "    await asyncio.sleep(1)\n"
            "class Owner:\n"
            "    def method(self):\n"
            "        return SerialCommunicator(port='x')\n"
        )
        self.assertEqual(
            blocking_call_sites(source),
            {
                ("async_bad", "read_text"),
                ("async_bad", "open"),
                ("async_bad", "glob"),
                ("async_bad", "os.path.exists"),
                ("async_bad", "sleep"),
                ("Owner.method", "SerialCommunicator"),
            },
        )


if __name__ == "__main__":
    unittest.main()
