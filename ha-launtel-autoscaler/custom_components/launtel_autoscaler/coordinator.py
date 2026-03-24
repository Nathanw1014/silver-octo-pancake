"""DataUpdateCoordinator for Launtel Autoscaler."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .launtel_api import LauntelApiClient, LauntelApiError, LauntelService

_LOGGER = logging.getLogger(__name__)


class LauntelCoordinator(DataUpdateCoordinator):
    """Coordinator to poll Launtel service state."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: LauntelApiClient,
        service_id: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.service_id = service_id
        self.service: LauntelService | None = None

    async def _async_update_data(self) -> LauntelService | None:
        """Fetch latest service data from Launtel."""
        try:
            service = await self.client.get_service(self.service_id)
            if service is None:
                raise UpdateFailed(
                    f"Service {self.service_id} not found on Launtel account"
                )
            self.service = service
            return service
        except LauntelApiError as err:
            raise UpdateFailed(f"Error communicating with Launtel: {err}") from err
