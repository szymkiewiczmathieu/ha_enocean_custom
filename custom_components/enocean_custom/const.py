"""Constants for the EnOcean Custom integration."""

import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "enocean_custom"
MANUFACTURER = "EnOcean"

DATA_ENOCEAN = DOMAIN
DATA_KNOWN_IDS = "known_ids"
DATA_LEARN_MANAGER = "learn_manager"
ENOCEAN_DONGLE = "dongle"

ERROR_INVALID_DONGLE_PATH = "invalid_dongle_path"
EVENT_DEVICE_LEARNED = f"{DOMAIN}_device_learned"
ISSUE_SERIAL_STOPPED = "serial_communicator_stopped"

SERVICE_LEARN = "learn"
SERVICE_SEND_TEACH_IN = "send_teach_in"

SIGNAL_DONGLE_STATUS = f"{DOMAIN}.dongle_status"
SIGNAL_RECEIVE_MESSAGE = f"{DOMAIN}.receive_message"

LEARN_TIMEOUT_DEFAULT = 60
LEARN_TIMEOUT_MIN = 15
LEARN_TIMEOUT_MAX = 300

LOGGER = logging.getLogger(__package__)

PLATFORMS: tuple[Platform, ...] = tuple(
    Platform(name) for name in ("binary_sensor", "climate", "light", "sensor", "switch")
)

# These platforms accept devices stored by the config-entry options flow.
UI_DEVICE_PLATFORMS = tuple(platform.value for platform in PLATFORMS)
