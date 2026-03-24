"""Switch platform for Launtel Autoscaler - toggle autoscaling."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .autoscaler import AutoscaleEngine
from .const import DOMAIN, MANUFACTURER
from .coordinator import LauntelCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: LauntelCoordinator = data["coordinator"]
    engine: AutoscaleEngine = data["engine"]
    service_id: str = data["service_id"]

    async_add_entities(
        [LauntelAutoscalerSwitch(coordinator, entry, engine, service_id)]
    )


class LauntelAutoscalerSwitch(CoordinatorEntity, SwitchEntity):
    """Toggle the autoscaler engine on or off."""

    def __init__(
        self,
        coordinator: LauntelCoordinator,
        entry: ConfigEntry,
        engine: AutoscaleEngine,
        service_id: str,
    ):
        super().__init__(coordinator)
        self._engine = engine
        self._attr_unique_id = f"{entry.entry_id}_autoscaler_switch"
        self._attr_name = "Launtel Autoscaler"
        self._attr_icon = "mdi:auto-fix"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, service_id)},
            name=f"Launtel Service {service_id}",
            manufacturer=MANUFACTURER,
            model="NBN Service",
        )

    @property
    def is_on(self) -> bool:
        return self._engine.config.enabled and self._engine.is_running

    async def async_turn_on(self, **kwargs) -> None:
        self._engine.config.enabled = True
        self._engine.start()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._engine.config.enabled = False
        self._engine.stop()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "wan_sensor": self._engine.config.wan_sensor_entity,
            "upgrade_threshold": self._engine.config.upgrade_threshold,
            "downgrade_threshold": self._engine.config.downgrade_threshold,
            "min_tier": self._engine.config.min_tier,
            "max_tier": self._engine.config.max_tier,
            "cooldown_mins": self._engine.config.cooldown_mins,
            "total_scale_events": len(self._engine.history),
        }
