"""Autoscaler engine for dynamic Launtel speed tier management.

Monitors a WAN utilisation sensor (from UniFi or any other source)
and triggers Launtel speed tier changes based on configurable thresholds.

Tiers are discovered dynamically from the Launtel portal (scraped),
not from a hardcoded list.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DEFAULT_COOLDOWN,
    DEFAULT_DOWNGRADE_SUSTAINED,
    DEFAULT_DOWNGRADE_THRESHOLD,
    DEFAULT_UPGRADE_SUSTAINED,
    DEFAULT_UPGRADE_THRESHOLD,
)
from .launtel_api import LauntelApiClient, LauntelSpeedChangeError

_LOGGER = logging.getLogger(__name__)


@dataclass
class AutoscaleConfig:
    """Configuration for the autoscaler."""

    enabled: bool = False
    wan_sensor_entity: str = ""
    upgrade_threshold: float = DEFAULT_UPGRADE_THRESHOLD
    downgrade_threshold: float = DEFAULT_DOWNGRADE_THRESHOLD
    upgrade_sustained_mins: int = DEFAULT_UPGRADE_SUSTAINED
    downgrade_sustained_mins: int = DEFAULT_DOWNGRADE_SUSTAINED
    cooldown_mins: int = DEFAULT_COOLDOWN
    schedule: dict[str, str] = field(default_factory=dict)


@dataclass
class ScaleEvent:
    """Record of a scaling event."""

    timestamp: datetime
    direction: str  # "up" or "down"
    from_tier: str
    to_tier: str
    reason: str
    utilisation: float


class AutoscaleEngine:
    """Monitors WAN utilisation and adjusts Launtel speed tiers.

    Tiers are sorted by download speed. Scaling up means picking the
    next higher tier, scaling down means the next lower tier.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: LauntelApiClient,
        coordinator,  # LauntelCoordinator — avoid circular import
        config: AutoscaleConfig,
    ):
        self.hass = hass
        self.client = client
        self.coordinator = coordinator
        self.config = config

        self._samples: deque[tuple[datetime, float]] = deque(maxlen=360)
        self._last_change: datetime | None = None
        self._current_psid: int | None = None
        self._cancel_timer = None
        self._history: deque[ScaleEvent] = deque(maxlen=50)
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._cancel_timer is not None

    @property
    def history(self) -> list[ScaleEvent]:
        return list(self._history)

    @property
    def current_utilisation(self) -> float | None:
        if not self._samples:
            return None
        return self._samples[-1][1]

    def start(self):
        if self._cancel_timer:
            return
        _LOGGER.info("Starting Launtel autoscaler engine")
        self._cancel_timer = async_track_time_interval(
            self.hass, self._async_evaluate, timedelta(seconds=30),
        )

    def stop(self):
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
            _LOGGER.info("Stopped Launtel autoscaler engine")

    def update_config(self, config: AutoscaleConfig):
        was_enabled = self.config.enabled
        self.config = config
        if config.enabled and not was_enabled:
            self.start()
        elif not config.enabled and was_enabled:
            self.stop()

    def set_current_psid(self, psid: int):
        self._current_psid = psid

    # ── Tier helpers (from scraped data) ────────────────────────────

    def _sorted_tiers(self) -> list[dict]:
        """Get available tiers sorted by download speed (ascending)."""
        svc = self.coordinator.service
        if not svc or not svc.available_tiers:
            return []
        return sorted(svc.available_tiers, key=lambda t: t["download"])

    def _current_tier_index(self) -> int:
        """Find the index of the current tier in the sorted list."""
        tiers = self._sorted_tiers()
        for i, t in enumerate(tiers):
            if t["psid"] == self._current_psid:
                return i
        return -1

    def _current_tier_name(self) -> str:
        tiers = self._sorted_tiers()
        for t in tiers:
            if t["psid"] == self._current_psid:
                return t["name"]
        return "unknown"

    def _next_tier_up(self) -> dict | None:
        tiers = self._sorted_tiers()
        idx = self._current_tier_index()
        if idx < 0 or idx >= len(tiers) - 1:
            return None
        return tiers[idx + 1]

    def _next_tier_down(self) -> dict | None:
        tiers = self._sorted_tiers()
        idx = self._current_tier_index()
        if idx <= 0:
            return None
        return tiers[idx - 1]

    # ── Core evaluation loop ────────────────────────────────────────

    @callback
    async def _async_evaluate(self, _now=None):
        if not self.config.enabled:
            return

        utilisation = self._read_wan_sensor()
        if utilisation is None:
            return

        now = datetime.now()
        self._samples.append((now, utilisation))

        # Check cooldown
        if self._last_change and (
            now - self._last_change < timedelta(minutes=self.config.cooldown_mins)
        ):
            return

        upgrade_window = timedelta(minutes=self.config.upgrade_sustained_mins)
        downgrade_window = timedelta(minutes=self.config.downgrade_sustained_mins)

        avg_short = self._average_utilisation(upgrade_window)
        avg_long = self._average_utilisation(downgrade_window)

        # Scale UP
        if avg_short is not None and avg_short >= self.config.upgrade_threshold:
            next_tier = self._next_tier_up()
            if next_tier:
                await self._execute_change(
                    next_tier,
                    utilisation,
                    reason=f"High utilisation ({avg_short:.0f}% avg over {self.config.upgrade_sustained_mins}m)",
                )

        # Scale DOWN
        elif avg_long is not None and avg_long <= self.config.downgrade_threshold:
            next_tier = self._next_tier_down()
            if next_tier:
                await self._execute_change(
                    next_tier,
                    utilisation,
                    reason=f"Low utilisation ({avg_long:.0f}% avg over {self.config.downgrade_sustained_mins}m)",
                )

    def _read_wan_sensor(self) -> float | None:
        entity_id = self.config.wan_sensor_entity
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _average_utilisation(self, window: timedelta) -> float | None:
        cutoff = datetime.now() - window
        values = [pct for ts, pct in self._samples if ts >= cutoff]
        if not values:
            return None
        return sum(values) / len(values)

    # ── Execution ───────────────────────────────────────────────────

    async def _execute_change(self, target_tier: dict, utilisation: float, reason: str):
        async with self._lock:
            svc = self.coordinator.service
            if not svc:
                return

            psid = target_tier["psid"]
            target_name = target_tier["name"]
            from_name = self._current_tier_name()

            try:
                success = await self.client.change_speed(svc, psid)
            except LauntelSpeedChangeError as err:
                _LOGGER.error("Speed change failed: %s", err)
                return

            if success:
                now = datetime.now()
                direction = "up" if target_tier["download"] > (
                    next((t["download"] for t in self._sorted_tiers() if t["psid"] == self._current_psid), 0)
                ) else "down"

                event = ScaleEvent(
                    timestamp=now,
                    direction=direction,
                    from_tier=from_name,
                    to_tier=target_name,
                    reason=reason,
                    utilisation=utilisation,
                )
                self._history.append(event)
                self._last_change = now
                self._current_psid = psid

                _LOGGER.info(
                    "Autoscaler: %s → %s (reason: %s)",
                    event.from_tier, event.to_tier, event.reason,
                )

                self.hass.bus.async_fire(
                    "launtel_autoscaler_speed_changed",
                    {
                        "direction": event.direction,
                        "from_tier": event.from_tier,
                        "to_tier": event.to_tier,
                        "reason": event.reason,
                        "utilisation": event.utilisation,
                    },
                )
