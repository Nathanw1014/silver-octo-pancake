"""Select platform for Launtel Autoscaler - manual tier picker."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .autoscaler import AutoscaleEngine
from .const import DOMAIN, MANUFACTURER
from .coordinator import LauntelCoordinator
from .launtel_api import LauntelApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: LauntelCoordinator = data["coordinator"]
    client: LauntelApiClient = data["client"]
    engine: AutoscaleEngine = data["engine"]
    sid: int = data["service_id"]

    async_add_entities(
        [LauntelTierSelect(coordinator, client, engine, entry, sid)]
    )


class LauntelTierSelect(CoordinatorEntity, SelectEntity):
    """Dropdown to manually select a Launtel speed tier.

    Options are populated dynamically from the scraped tier list.
    """

    def __init__(self, coordinator, client, engine, entry, service_id):
        super().__init__(coordinator)
        self._client = client
        self._engine = engine
        self._attr_unique_id = f"{entry.entry_id}_tier_select"
        self._attr_name = "Launtel Speed Tier"
        self._attr_icon = "mdi:speedometer"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(service_id))},
            name=f"Launtel Service {service_id}",
            manufacturer=MANUFACTURER,
            model="NBN Service",
        )

    @property
    def options(self) -> list[str]:
        """Build options from available tiers (scraped from portal)."""
        svc = self.coordinator.service
        if not svc or not svc.available_tiers:
            return []
        sorted_tiers = sorted(svc.available_tiers, key=lambda t: t["download"])
        return [t["name"] for t in sorted_tiers]

    @property
    def current_option(self) -> str | None:
        svc = self.coordinator.service
        if svc:
            return svc.current_tier or None
        return None

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a new speed tier."""
        svc = self.coordinator.service
        if not svc or not svc.available_tiers:
            _LOGGER.error("No tier data available")
            return

        # Find the tier matching the selected name
        selected = None
        for tier in svc.available_tiers:
            if tier["name"] == option:
                selected = tier
                break

        if selected is None:
            _LOGGER.error("Could not find tier matching '%s'", option)
            return

        psid = selected["psid"]
        await self._client.change_speed(svc, psid)
        self._engine.set_current_psid(psid)
        await self.coordinator.async_request_refresh()
