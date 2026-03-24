"""Sensor platform for Launtel Autoscaler."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
    sid: int = data["service_id"]

    async_add_entities([
        LauntelCurrentTierSensor(coordinator, entry, sid),
        LauntelDownloadSpeedSensor(coordinator, entry, sid),
        LauntelUploadSpeedSensor(coordinator, entry, sid),
        LauntelDailyCostSensor(coordinator, entry, sid),
        LauntelServiceStatusSensor(coordinator, entry, sid),
        LauntelAutoscalerUtilisationSensor(coordinator, entry, engine, sid),
        LauntelLastScaleEventSensor(coordinator, entry, engine, sid),
    ])


def _device_info(service_id: int) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, str(service_id))},
        name=f"Launtel Service {service_id}",
        manufacturer=MANUFACTURER,
        model="NBN Service",
    )


class LauntelBaseSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, service_id, key, name):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = _device_info(service_id)


class LauntelCurrentTierSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, sid):
        super().__init__(coordinator, entry, sid, "current_tier", "Launtel Current Tier")
        self._attr_icon = "mdi:speedometer"

    @property
    def native_value(self):
        svc = self.coordinator.service
        return svc.current_tier if svc else None


class LauntelDownloadSpeedSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, sid):
        super().__init__(coordinator, entry, sid, "download_speed", "Launtel Download Speed")
        self._attr_native_unit_of_measurement = "Mbps"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:download"

    @property
    def native_value(self):
        svc = self.coordinator.service
        return svc.download_mbps if svc else None


class LauntelUploadSpeedSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, sid):
        super().__init__(coordinator, entry, sid, "upload_speed", "Launtel Upload Speed")
        self._attr_native_unit_of_measurement = "Mbps"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:upload"

    @property
    def native_value(self):
        svc = self.coordinator.service
        return svc.upload_mbps if svc else None


class LauntelDailyCostSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, sid):
        super().__init__(coordinator, entry, sid, "daily_cost", "Launtel Daily Cost")
        self._attr_native_unit_of_measurement = "AUD"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:currency-usd"

    @property
    def native_value(self):
        svc = self.coordinator.service
        return svc.daily_cost if svc else None

    @property
    def extra_state_attributes(self):
        svc = self.coordinator.service
        if svc and svc.daily_cost:
            return {"monthly_estimate": round(svc.daily_cost * 30.44, 2)}
        return {}


class LauntelServiceStatusSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, sid):
        super().__init__(coordinator, entry, sid, "service_status", "Launtel Service Status")
        self._attr_icon = "mdi:lan-connect"

    @property
    def native_value(self):
        svc = self.coordinator.service
        return svc.status if svc else None


class LauntelAutoscalerUtilisationSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, engine: AutoscaleEngine, sid):
        super().__init__(coordinator, entry, sid, "wan_utilisation", "WAN Utilisation (Autoscaler)")
        self._engine = engine
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:chart-line"

    @property
    def native_value(self):
        return self._engine.current_utilisation

    @property
    def extra_state_attributes(self):
        return {
            "autoscaler_enabled": self._engine.config.enabled,
            "upgrade_threshold": self._engine.config.upgrade_threshold,
            "downgrade_threshold": self._engine.config.downgrade_threshold,
        }


class LauntelLastScaleEventSensor(LauntelBaseSensor):
    def __init__(self, coordinator, entry, engine: AutoscaleEngine, sid):
        super().__init__(coordinator, entry, sid, "last_scale_event", "Launtel Last Scale Event")
        self._engine = engine
        self._attr_icon = "mdi:swap-vertical"

    @property
    def native_value(self):
        history = self._engine.history
        if not history:
            return "No events"
        last = history[-1]
        return f"{last.direction}: {last.from_tier} → {last.to_tier}"

    @property
    def extra_state_attributes(self):
        history = self._engine.history
        if not history:
            return {}
        last = history[-1]
        return {
            "timestamp": last.timestamp.isoformat(),
            "direction": last.direction,
            "from_tier": last.from_tier,
            "to_tier": last.to_tier,
            "reason": last.reason,
            "utilisation_at_change": last.utilisation,
            "total_events": len(history),
        }
