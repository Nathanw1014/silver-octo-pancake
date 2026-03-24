"""Config flow for Launtel Autoscaler."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

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
    TIER_ORDER,
)
from .launtel_api import LauntelApiClient, LauntelAuthError

_LOGGER = logging.getLogger(__name__)


class LauntelAutoscalerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Launtel Autoscaler."""

    VERSION = 1

    def __init__(self):
        self._username: str = ""
        self._password: str = ""
        self._client: LauntelApiClient | None = None
        self._services: list = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: Launtel credentials."""
        errors = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            client = LauntelApiClient(self._username, self._password)
            try:
                await client.authenticate()
                self._client = client
                self._services = await client.get_services()

                if not self._services:
                    errors["base"] = "no_services"
                elif len(self._services) == 1:
                    svc = self._services[0]
                    await client.close()
                    return self.async_create_entry(
                        title=f"Launtel - {svc.name}",
                        data={
                            CONF_USERNAME: self._username,
                            CONF_PASSWORD: self._password,
                            CONF_SERVICE_ID: svc.service_id,
                        },
                    )
                else:
                    return await self.async_step_select_service()

            except LauntelAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "cannot_connect"
            finally:
                if errors and client:
                    await client.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_service(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: Select which service to manage (if multiple)."""
        if user_input is not None:
            service_id = int(user_input[CONF_SERVICE_ID])
            svc = next(
                (s for s in self._services if s.service_id == service_id),
                self._services[0],
            )
            if self._client:
                await self._client.close()
            return self.async_create_entry(
                title=f"Launtel - {svc.name}",
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_SERVICE_ID: service_id,
                },
            )

        service_options = {
            str(s.service_id): f"{s.name} ({s.status})"
            for s in self._services
        }

        return self.async_show_form(
            step_id="select_service",
            data_schema=vol.Schema(
                {vol.Required(CONF_SERVICE_ID): vol.In(service_options)}
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LauntelOptionsFlow()


class LauntelOptionsFlow(config_entries.OptionsFlow):
    """Options flow for autoscaler thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "autoscaler_enabled",
                        default=opts.get("autoscaler_enabled", False),
                    ): bool,
                    vol.Optional(
                        CONF_WAN_SENSOR,
                        default=opts.get(CONF_WAN_SENSOR, ""),
                    ): str,
                    vol.Optional(
                        CONF_UPGRADE_THRESHOLD,
                        default=opts.get(
                            CONF_UPGRADE_THRESHOLD, DEFAULT_UPGRADE_THRESHOLD
                        ),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_DOWNGRADE_THRESHOLD,
                        default=opts.get(
                            CONF_DOWNGRADE_THRESHOLD, DEFAULT_DOWNGRADE_THRESHOLD
                        ),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_UPGRADE_SUSTAINED_MINS,
                        default=opts.get(
                            CONF_UPGRADE_SUSTAINED_MINS, DEFAULT_UPGRADE_SUSTAINED
                        ),
                    ): _cv_positive_int,
                    vol.Optional(
                        CONF_DOWNGRADE_SUSTAINED_MINS,
                        default=opts.get(
                            CONF_DOWNGRADE_SUSTAINED_MINS,
                            DEFAULT_DOWNGRADE_SUSTAINED,
                        ),
                    ): _cv_positive_int,
                    vol.Optional(
                        CONF_COOLDOWN_MINS,
                        default=opts.get(CONF_COOLDOWN_MINS, DEFAULT_COOLDOWN),
                    ): _cv_positive_int,
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): _cv_positive_int,
                }
            ),
        )


def _cv_positive_int(value):
    """Validate positive integer."""
    val = int(value)
    if val <= 0:
        raise vol.Invalid("Must be a positive integer")
    return val
