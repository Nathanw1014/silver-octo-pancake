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
from .const import DOMAIN, MANUFACTURER, TIER_ORDER
from .coordinator import LauntelCoordinator
from .launtel_api import LauntelApiClient, LauntelTier

_LOGGER = logging.getLogger(__name__)

TIER_LABELS = {t.tier_id: t.label for t in LauntelTier}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: LauntelCoordinator = data["coordinator"]
    client: LauntelApiClient = data["client"]
    engine: AutoscaleEngine = data["engine"]
    service_id: str = data["service_id"]

    async_add_entities(
        [LauntelTierSelect(coordinator, client, engine, entry, service_id)]
    )


class LauntelTierSelect(CoordinatorEntity, SelectEntity):
    """Dropdown to manually select a Launtel speed tier."""

    def __init__(
        self,
        coordinator: LauntelCoordinator,
        client: LauntelApiClient,
        engine: AutoscaleEngine,
        entry: ConfigEntry,
        service_id: str,
    ):
        super().__init__(coordinator)
        self._client = client
        self._engine = engine
        self._service_id = service_id
        self._attr_unique_id = f"{entry.entry_id}_tier_select"
        self._attr_name = "Launtel Speed Tier"
        self._attr_icon = "mdi:speedometer"
        self._attr_options = [TIER_LABELS.get(t, t) for t in TIER_ORDER]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, service_id)},
            name=f"Launtel Service {service_id}",
            manufacturer=MANUFACTURER,
            model="NBN Service",
        )

    @property
    def current_option(self) -> str | None:
        svc = self.coordinator.service
        if svc:
            return svc.current_tier
        return None

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a new speed tier."""
        # Reverse-lookup tier_id from label
        tier_id = None
        for tid, label in TIER_LABELS.items():
            if label == option or tid == option:
                tier_id = tid
                break

        if tier_id is None:
            _LOGGER.error("Could not map selection '%s' to a tier_id", option)
            return

        # Get the LauntelTier enum
        try:
            tier_enum = next(t for t in LauntelTier if t.tier_id == tier_id)
        except StopIteration:
            _LOGGER.error("Unknown tier_id: %s", tier_id)
            return

        # Fetch available tiers to get the psid
        tiers = await self._client.get_available_tiers(self._service_id)
        psid = None
        for t in tiers:
            if (
                t.get("download") == tier_enum.download_mbps
                and t.get("upload") == tier_enum.upload_mbps
            ):
                psid = t["psid"]
                break

        if psid is None:
            _LOGGER.error(
                "Tier %s (%s/%s) not available at this address",
                tier_id,
                tier_enum.download_mbps,
                tier_enum.upload_mbps,
            )
            return

        await self._client.change_speed(self._service_id, psid)
        self._engine.set_current_tier(tier_id)
        await self.coordinator.async_request_refresh()
