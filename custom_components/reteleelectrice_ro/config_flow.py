"""Config flow for Rețele Electrice România."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback

from .api import AuthenticationError, PortalError, ReteleElectriceClient
from .const import CONF_EMAIL, CONF_IMPORT_PRICE, DEFAULT_IMPORT_PRICE, DOMAIN


async def _validate_credentials(hass: HomeAssistant, email: str, password: str) -> None:
    client = ReteleElectriceClient(email, password)
    try:
        await client.async_login()
    finally:
        await client.async_close()


class ReteleElectriceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup through the Home Assistant UI."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ReteleElectriceOptionsFlow()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an expired portal session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a replacement password for the existing account."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input:
            try:
                await _validate_credentials(
                    self.hass,
                    entry.data[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (PortalError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))}
            ),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input:
            email = user_input[CONF_EMAIL].strip().lower()
            try:
                await _validate_credentials(self.hass, email, user_input[CONF_PASSWORD])
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (PortalError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Rețele Electrice România",
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1)),
                }
            ),
            errors=errors,
        )


def _finite_price(value: float) -> float:
    """Reject nonfinite tariffs before they can reach recorder statistics."""
    if not math.isfinite(value):
        raise vol.Invalid("Price must be finite")
    return value


class ReteleElectriceOptionsFlow(config_entries.OptionsFlow):
    """Set the flat tariff used to estimate costs for retained history."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_IMPORT_PRICE, default=self.config_entry.options.get(
                    CONF_IMPORT_PRICE, DEFAULT_IMPORT_PRICE
                )): vol.All(vol.Coerce(float), vol.Range(min=0), _finite_price),
            }),
        )
