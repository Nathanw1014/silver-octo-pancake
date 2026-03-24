"""Autoscaler engine for dynamic Launtel speed tier management.

Monitors a WAN utilisation sensor (from UniFi or any other source)
and triggers Launtel speed tier changes based on configurable thresholds.
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
    TIER_ORDER,
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
    min_tier: str = "100_20"
    max_tier: str = "1000_50"
    cooldown_mins: int = DEFAULT_COOLDOWN
    # Optional: time-based schedule overrides
    # e.g. {"06:00-09:00": "100_20", "18:00-23:00": "250_25"}
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
    """Monitors WAN utilisation and adjusts Launtel speed tiers."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: LauntelApiClient,
        service_id: str,
        config: AutoscaleConfig,
    ):
        self.hass = hass
        self.client = client
        self.service_id = service_id
        self.config = config

        # Rolling window of utilisation samples (timestamp, percent)
        self._samples: deque[tuple[datetime, float]] = deque(maxlen=360)
        self._last_change: datetime | None = None
        self._current_tier: str | None = None
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
        """Start the autoscaler polling loop."""
        if self._cancel_timer:
            return

        _LOGGER.info("Starting Launtel autoscaler engine")
        self._cancel_timer = async_track_time_interval(
            self.hass,
            self._async_evaluate,
            timedelta(seconds=30),
        )

    def stop(self):
        """Stop the autoscaler."""
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
            _LOGGER.info("Stopped Launtel autoscaler engine")

    def update_config(self, config: AutoscaleConfig):
        """Hot-update the autoscaler config."""
        was_enabled = self.config.enabled
        self.config = config

        if config.enabled and not was_enabled:
            self.start()
        elif not config.enabled and was_enabled:
            self.stop()

    # ── Core evaluation loop ────────────────────────────────────────

    @callback
    async def _async_evaluate(self, _now=None):
        """Evaluate current WAN usage and decide whether to scale."""
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

        # Check schedule overrides first
        scheduled_tier = self._check_schedule(now)
        if scheduled_tier and scheduled_tier != self._current_tier:
            await self._execute_change(
                scheduled_tier,
                utilisation,
                reason=f"Schedule override ({now.strftime('%H:%M')})",
            )
            return

        # Calculate sustained averages
        upgrade_window = timedelta(minutes=self.config.upgrade_sustained_mins)
        downgrade_window = timedelta(minutes=self.config.downgrade_sustained_mins)

        avg_short = self._average_utilisation(upgrade_window)
        avg_long = self._average_utilisation(downgrade_window)

        # Scale UP: sustained high utilisation
        if avg_short is not None and avg_short >= self.config.upgrade_threshold:
            next_tier = self._next_tier_up()
            if next_tier and next_tier != self._current_tier:
                await self._execute_change(
                    next_tier,
                    utilisation,
                    reason=f"High utilisation ({avg_short:.0f}% avg over {self.config.upgrade_sustained_mins}m)",
                )

        # Scale DOWN: sustained low utilisation
        elif avg_long is not None and avg_long <= self.config.downgrade_threshold:
            next_tier = self._next_tier_down()
            if next_tier and next_tier != self._current_tier:
                await self._execute_change(
                    next_tier,
                    utilisation,
                    reason=f"Low utilisation ({avg_long:.0f}% avg over {self.config.downgrade_sustained_mins}m)",
                )

    def _read_wan_sensor(self) -> float | None:
        """Read the WAN utilisation sensor from HA state."""
        entity_id = self.config.wan_sensor_entity
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None

        try:
            return float(state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Could not parse WAN sensor value: %s", state.state)
            return None

    def _average_utilisation(self, window: timedelta) -> float | None:
        """Calculate average utilisation over a time window."""
        cutoff = datetime.now() - window
        values = [pct for ts, pct in self._samples if ts >= cutoff]
        if not values:
            return None
        return sum(values) / len(values)

    def _check_schedule(self, now: datetime) -> str | None:
        """Check if a schedule override applies right now."""
        current_time = now.strftime("%H:%M")
        for time_range, tier in self.config.schedule.items():
            try:
                start, end = time_range.split("-")
                if start <= current_time <= end:
                    return tier
            except ValueError:
                continue
        return None

    # ── Tier navigation ─────────────────────────────────────────────

    def _tier_index(self, tier_id: str) -> int:
        try:
            return TIER_ORDER.index(tier_id)
        except ValueError:
            return -1

    def _next_tier_up(self) -> str | None:
        if not self._current_tier:
            return None
        idx = self._tier_index(self._current_tier)
        max_idx = self._tier_index(self.config.max_tier)
        if idx < 0 or max_idx < 0 or idx >= max_idx:
            return None
        return TIER_ORDER[idx + 1]

    def _next_tier_down(self) -> str | None:
        if not self._current_tier:
            return None
        idx = self._tier_index(self._current_tier)
        min_idx = self._tier_index(self.config.min_tier)
        if idx < 0 or min_idx < 0 or idx <= min_idx:
            return None
        return TIER_ORDER[idx - 1]

    # ── Execution ───────────────────────────────────────────────────

    async def _execute_change(
        self, target_tier: str, utilisation: float, reason: str
    ):
        """Execute a speed tier change via the Launtel API."""
        async with self._lock:
            # We need the psid for the target tier — look it up from
            # the service's available_tiers. If we don't have it cached,
            # fetch from the API.
            try:
                tiers = await self.client.get_available_tiers(self.service_id)
            except Exception:
                _LOGGER.error("Failed to fetch available tiers")
                return

            # Match by download speed from tier_id
            from .launtel_api import LauntelTier

            try:
                target_enum = next(
                    t for t in LauntelTier if t.tier_id == target_tier
                )
            except StopIteration:
                _LOGGER.error("Unknown tier: %s", target_tier)
                return

            # Find PSID from available tiers
            psid = None
            for t in tiers:
                if (
                    t.get("download") == target_enum.download_mbps
                    and t.get("upload") == target_enum.upload_mbps
                ):
                    psid = t["psid"]
                    break

            if psid is None:
                _LOGGER.warning(
                    "Could not find PSID for tier %s — available: %s",
                    target_tier,
                    tiers,
                )
                return

            try:
                success = await self.client.change_speed(self.service_id, psid)
            except LauntelSpeedChangeError as err:
                _LOGGER.error("Speed change failed: %s", err)
                return

            if success:
                now = datetime.now()
                event = ScaleEvent(
                    timestamp=now,
                    direction="up" if self._tier_index(target_tier) > self._tier_index(self._current_tier or "") else "down",
                    from_tier=self._current_tier or "unknown",
                    to_tier=target_tier,
                    reason=reason,
                    utilisation=utilisation,
                )
                self._history.append(event)
                self._last_change = now
                self._current_tier = target_tier

                _LOGGER.info(
                    "Autoscaler: %s → %s (reason: %s)",
                    event.from_tier,
                    event.to_tier,
                    event.reason,
                )

                # Fire a HA event so automations can react
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

    def set_current_tier(self, tier_id: str):
        """Set the current tier (called by coordinator after API poll)."""
        self._current_tier = tier_id
