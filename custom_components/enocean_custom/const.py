"""Constants for the EnOcean Custom integration."""

import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "enocean_custom"
MANUFACTURER = "EnOcean"

# Config entry schema version. The minor version is bumped when stored options
# are rewritten in a backward-compatible way (see migration.py); a downgrade to
# an older release still loads the entry.
#
# Minor 3 (v2.11.0): every radio device becomes one config subentry of type
# ``device``; ``options.ui_devices`` is kept as a complete, downgrade-safe
# mirror of the rows those subentries hold.
CONFIG_ENTRY_VERSION: Final = 1
CONFIG_ENTRY_MINOR_VERSION: Final = 3

# The one subentry type this integration declares: one EnOcean radio device
# (one four-byte sender), holding every UI row configured for that sender.
SUBENTRY_TYPE_DEVICE: Final = "device"

# ``hass.data[DATA_ENOCEAN]`` holds only integration-wide state that is not
# tied to the config entry: YAML-claimed sender ids, the teach-in manager and
# the YAML migration inventory. The dongle and its inbox live on
# ``ConfigEntry.runtime_data`` (see ``runtime.py``).
DATA_ENOCEAN = DOMAIN
DATA_KNOWN_IDS = "known_ids"
DATA_LEARN_MANAGER = "learn_manager"

ERROR_INVALID_DONGLE_PATH = "invalid_dongle_path"
EVENT_DEVICE_LEARNED = f"{DOMAIN}_device_learned"
ISSUE_SERIAL_STOPPED = "serial_communicator_stopped"
# Raised per silent sender when silence detection is enabled for its category.
ISSUE_SILENT_DEVICE = "silent_device"

SERVICE_LEARN = "learn"
SERVICE_SEND_TEACH_IN = "send_teach_in"

SIGNAL_DONGLE_STATUS = f"{DOMAIN}.dongle_status"
SIGNAL_RECEIVE_MESSAGE = f"{DOMAIN}.receive_message"
# Fired on the event loop when the dongle learns something about itself:
# firmware version, Base ID, repeater state.
SIGNAL_GATEWAY_UPDATE = f"{DOMAIN}.gateway_update"
# Fired on the event loop with ``(entry_id, subentry_id)`` when a device
# subentry was added or rewritten while the entry is loaded: every platform
# creates that device's entities without the entry being reloaded.
SIGNAL_SUBENTRY_SETUP = f"{DOMAIN}.subentry_setup"

LEARN_TIMEOUT_DEFAULT = 60
LEARN_TIMEOUT_MIN = 15
LEARN_TIMEOUT_MAX = 300

LOGGER = logging.getLogger(__package__)

# These platforms accept devices stored by the config-entry options flow: a
# UI row names one of them as its ``platform``.
UI_DEVICE_PLATFORMS = ("binary_sensor", "climate", "light", "sensor", "switch")

# Every platform the config entry forwards: the row platforms, ``select``
# (the dongle's own repeater setting, never a UI row), ``event`` (one
# companion entity per rocker sender, never a UI row either) and ``button``
# (one "refresh state" action per D2-01 actuator channel, see d2_01.py).
PLATFORMS: tuple[Platform, ...] = tuple(
    Platform(name) for name in (*UI_DEVICE_PLATFORMS, "select", "event", "button")
)
FORWARDED_PLATFORMS = tuple(platform.value for platform in PLATFORMS)
