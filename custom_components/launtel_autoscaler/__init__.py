"""Launtel Autoscaler - Home Assistant Integration.

Monitors UniFi WAN utilisation and dynamically adjusts your
Launtel NBN speed tier to match demand.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .autoscaler import AutoscaleConfig, AutoscaleEngine
from .const import (
    CONF_COOLDOWN_MINS,
    CONF_DOWNGRADE_SUSTAINED_MINS,
    CONF_DOWNGRADE_THRESHOLD,
    CONF_MAX_TIER,
    CONF_MIN_TIER,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SERVICE_ID,
    CONF_UPGRADE_SUSTAINED_MINS,
    CONF_UPGRADE_THRESHOLD,
    CONF_USERNAME,
    CONF_WAN_SENSOR,
    DEFAULT_COOLDOWN,
    DEFAULT_DOWNGRADE_SUSTAINED,
    DEFAULT_DOWNGRADE_THRESHOLD,
    DEFAULT_MAX_TIER,
    DEFAULT_MIN_TIER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPGRADE_SUSTAINED,
    DEFAULT_UPGRADE_THRESHOLD,
    DOMAIN,
    SERVICE_CHANGE_SPEED,
    SERVICE_SET_AUTOSCALE,
)
from .coordinator import LauntelCoordinator
from .launtel_api import LauntelApiClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Launtel Autoscaler from a config entry."""

    client = LauntelApiClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )
    await client.authenticate()

    service_id = entry.data[CONF_SERVICE_ID]

    coordinator = LauntelCoordinator(
        hass,
        client,
        service_id,
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    # Build autoscale config from options
    autoscale_config = AutoscaleConfig(
        enabled=entry.options.get("autoscaler_enabled", False),
        wan_sensor_entity=entry.options.get(CONF_WAN_SENSOR, ""),
        upgrade_threshold=entry.options.get(
            CONF_UPGRADE_THRESHOLD, DEFAULT_UPGRADE_THRESHOLD
        ),
        downgrade_threshold=entry.options.get(
            CONF_DOWNGRADE_THRESHOLD, DEFAULT_DOWNGRADE_THRESHOLD
        ),
        upgrade_sustained_mins=entry.options.get(
            CONF_UPGRADE_SUSTAINED_MINS, DEFAULT_UPGRADE_SUSTAINED
        ),
        downgrade_sustained_mins=entry.options.get(
            CONF_DOWNGRADE_SUSTAINED_MINS, DEFAULT_DOWNGRADE_SUSTAINED
        ),
        cooldown_mins=entry.options.get(CONF_COOLDOWN_MINS, DEFAULT_COOLDOWN),
        schedule=entry.options.get("schedule", {}),
    )

    engine = AutoscaleEngine(hass, client, coordinator, autoscale_config)

    # Sync current tier from coordinator's scraped data
    if coordinator.service and coordinator.service.available_tiers:
        svc = coordinator.service
        if svc.current_psid:
            engine.set_current_psid(svc.current_psid)

    if autoscale_config.enabled:
        engine.start()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "engine": engine,
        "service_id": service_id,
    }

    _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].pop(entry.entry_id, {})
    engine: AutoscaleEngine | None = data.get("engine")
    if engine:
        engine.stop()

    client: LauntelApiClient | None = data.get("client")
    if client:
        await client.close()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _register_services(hass: HomeAssistant):
    """Register HA services for manual speed control."""

    if hass.services.has_service(DOMAIN, SERVICE_CHANGE_SPEED):
        return

    async def handle_change_speed(call: ServiceCall):
        """Handle launtel_autoscaler.change_speed service call."""
        entry_id = call.data.get("entry_id")
        psid = call.data.get("psid")

        for eid, data in hass.data.get(DOMAIN, {}).items():
            if entry_id and eid != entry_id:
                continue

            client: LauntelApiClient = data["client"]
            coordinator: LauntelCoordinator = data["coordinator"]

            if not coordinator.service:
                _LOGGER.error("No service data available")
                return

            if psid:
                await client.change_speed(coordinator.service, psid)
                await coordinator.async_request_refresh()

    async def handle_set_autoscale(call: ServiceCall):
        """Enable/disable autoscaler or update thresholds at runtime."""
        for eid, data in hass.data.get(DOMAIN, {}).items():
            engine: AutoscaleEngine = data["engine"]
            new_config = AutoscaleConfig(
                enabled=call.data.get("enabled", engine.config.enabled),
                wan_sensor_entity=call.data.get(
                    "wan_sensor", engine.config.wan_sensor_entity
                ),
                upgrade_threshold=call.data.get(
                    "upgrade_threshold", engine.config.upgrade_threshold
                ),
                downgrade_threshold=call.data.get(
                    "downgrade_threshold", engine.config.downgrade_threshold
                ),
                upgrade_sustained_mins=call.data.get(
                    "upgrade_sustained_mins", engine.config.upgrade_sustained_mins
                ),
                downgrade_sustained_mins=call.data.get(
                    "downgrade_sustained_mins",
                    engine.config.downgrade_sustained_mins,
                ),
                cooldown_mins=call.data.get(
                    "cooldown_mins", engine.config.cooldown_mins
                ),
                schedule=call.data.get("schedule", engine.config.schedule),
            )
            engine.update_config(new_config)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHANGE_SPEED,
        handle_change_speed,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("psid"): cv.positive_int,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AUTOSCALE,
        handle_set_autoscale,
        schema=vol.Schema(
            {
                vol.Optional("enabled"): cv.boolean,
                vol.Optional("wan_sensor"): cv.entity_id,
                vol.Optional("upgrade_threshold"): vol.Coerce(float),
                vol.Optional("downgrade_threshold"): vol.Coerce(float),
                vol.Optional("upgrade_sustained_mins"): cv.positive_int,
                vol.Optional("downgrade_sustained_mins"): cv.positive_int,
                vol.Optional("cooldown_mins"): cv.positive_int,
            }
        ),
    )
