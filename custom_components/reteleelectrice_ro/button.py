"""Buttons for Rețele Electrice România."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import AuthenticationError, PortalError
from .const import DOMAIN
from .coordinator import ReteleElectriceCoordinator


PARALLEL_UPDATES = 0


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
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_actualizare_instantanee"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, pod)},
            "name": f"Rețele Electrice {pod}",
            "manufacturer": "Rețele Electrice România",
        }

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"POD": self._pod, "attribution": "Data from contulmeu.reteleelectrice.ro"}

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_request_instant_refresh(self._pod)
        except AuthenticationError as err:
            self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "Authentication expired; Home Assistant started reauthentication"
            ) from err
        except PortalError as err:
            raise HomeAssistantError(
                f"Instant smart-meter refresh failed for {self._pod}"
            ) from err


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    pods = (coordinator.data or {}).get("pods", {})
    pod_names = []
    if isinstance(pods, dict):
        for pod_name, pod_data in pods.items():
            summary = pod_data.get("summary", {}) if isinstance(pod_data, dict) else {}
            if ReteleElectriceCoordinator._is_smart_meter(summary):
                pod_names.append(str(pod_name))
    async_add_entities(
        [RefreshButton(coordinator)]
        + [InstantRefreshButton(coordinator, pod) for pod in pod_names]
    )
