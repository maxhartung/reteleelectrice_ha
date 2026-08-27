"""Home Assistant integration for Rețele Electrice România."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ReteleElectriceClient
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import ReteleElectriceCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


@dataclass
class RuntimeData:
    """Runtime objects owned by one config entry."""

    client: ReteleElectriceClient
    coordinator: ReteleElectriceCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = ReteleElectriceClient(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        async_get_clientsession(hass),
    )
    coordinator = ReteleElectriceCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = RuntimeData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.client._logged_in = False
    return unloaded
