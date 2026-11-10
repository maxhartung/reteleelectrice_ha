"""Home Assistant integration for Rețele Electrice România."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ReteleElectriceClient
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import ReteleElectriceCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class RuntimeData:
    """Runtime objects owned by one config entry."""

    client: ReteleElectriceClient
    coordinator: ReteleElectriceCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = ReteleElectriceClient(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        # Keep Salesforce cookies separate from other entries and integrations.
        async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar()),
    )
    coordinator = ReteleElectriceCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = RuntimeData(client, coordinator)
    # Remove obsolete entities from this integration's entry on upgrade.
    from homeassistant.helpers import entity_registry as er
    from .sensor import UNIQUE_SUFFIXES

    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform == DOMAIN and not (
            entity.domain == "sensor" and any(
                entity.unique_id.endswith(f"_{suffix}") for suffix in UNIQUE_SUFFIXES.values()
            )
        ):
            registry.async_remove(entity.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reimport estimated costs when the configured tariff changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_close()
    return unloaded
