"""Buttons for Rețele Electrice România."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ReteleElectriceCoordinator


class RefreshButton(CoordinatorEntity[ReteleElectriceCoordinator], ButtonEntity):
    """Force a normal coordinated refresh."""

    _attr_has_entity_name = True
    _attr_name = "Actualizează datele"

    def __init__(self, coordinator: ReteleElectriceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_refresh"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": "Rețele Electrice România",
            "manufacturer": "Rețele Electrice România",
        }

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class InstantRefreshButton(CoordinatorEntity[ReteleElectriceCoordinator], ButtonEntity):
    """Request fresh smart-meter values for one POD."""

    _attr_has_entity_name = True
    _attr_name = "Actualizare valori instantanee"

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{pod}_instant_refresh"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, pod)},
            "name": f"Rețele Electrice {pod}",
            "manufacturer": "Rețele Electrice România",
        }

    async def async_press(self) -> None:
        await self.coordinator.async_request_instant_refresh(self._pod)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    pods = (coordinator.data or {}).get("pods", {})
    pod_names = [str(name) for name in pods] if isinstance(pods, dict) else []
    async_add_entities(
        [RefreshButton(coordinator)]
        + [InstantRefreshButton(coordinator, pod) for pod in pod_names]
    )
