"""Constants for the ENOcean integration."""

import logging

from homeassistant.const import Platform

DOMAIN = "enocean_custom"
DATA_ENOCEAN = "enocean_custom"
ENOCEAN_DONGLE = "dongle"
DATA_KNOWN_IDS = "known_ids"
DATA_LEARN_MANAGER = "learn_manager"

ERROR_INVALID_DONGLE_PATH = "invalid_dongle_path"

SIGNAL_RECEIVE_MESSAGE = "enocean_custom.receive_message"
SIGNAL_DONGLE_STATUS = "enocean_custom.dongle_status"

ISSUE_SERIAL_STOPPED = "serial_communicator_stopped"

EVENT_DEVICE_LEARNED = "enocean_custom_device_learned"
SERVICE_LEARN = "learn"

LEARN_TIMEOUT_DEFAULT = 60
LEARN_TIMEOUT_MIN = 15
LEARN_TIMEOUT_MAX = 300

LOGGER = logging.getLogger(__package__)

PLATFORMS = [
    Platform.LIGHT,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Platforms that can be fully managed (added/removed) from the config entry
# options UI. climate and sensor stay YAML-only: teach-in scope is limited to
# the three platforms simple enough to describe with a short device form.
UI_DEVICE_PLATFORMS = ("binary_sensor", "switch", "light")
