"""Constants for the OBD BLE integration."""

from __future__ import annotations

DOMAIN = "obd_ble"

CONF_PROFILE = "profile"
CONF_VIN = "vin"
CONF_IDLE_INTERVAL = "idle_interval"
CONF_CHARGING_INTERVAL = "charging_interval"

DEFAULT_IDLE_INTERVAL_SECONDS = 300
DEFAULT_CHARGING_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 30

# Dashboard card served by the integration.
FRONTEND_URL_BASE = "/obd_ble_frontend"
FRONTEND_CARD_FILENAME = "obd-ble-card.js"
FRONTEND_VERSION = "0.2.0"  # bump with manifest version to bust browser caches
