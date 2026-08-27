"""Sensors for Rețele Electrice România."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ReteleElectriceCoordinator


def _walk_values(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        for child in value.values():
            found = _walk_values(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_values(child, keys)
            if found is not None:
                return found
    return None


def _register_value(value: Any, register: str) -> Any:
    """Find a value in either a keyed or typed energy-reading structure."""
    if isinstance(value, dict):
        for key in (register, register.upper(), register.lower()):
            if key in value:
                return value[key]

        register_type = next(
            (
                value.get(key)
                for key in (
                    "energyType",
                    "ENERGY_TYPE",
                    "register",
                    "REGISTER",
                    "code",
                    "CODE",
                )
                if value.get(key) is not None
            ),
            None,
        )
        if str(register_type).upper() == register.upper():
            for key in (
                "value",
                "VALUE",
                "reading",
                "READING",
                "energyValue",
                "ENERGY_VALUE",
                "index",
                "INDEX",
            ):
                if key in value:
                    return value[key]

        for child in value.values():
            found = _register_value(child, register)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _register_value(child, register)
            if found is not None:
                return found
    return None


def _to_number(value: Any) -> float | None:
    """Convert portal numbers, including Romanian comma decimals, to float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


class ReteleElectriceSensor(CoordinatorEntity[ReteleElectriceCoordinator], SensorEntity):
    """Base entity for coordinator-backed sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str, name: str) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_name = name
        self._attr_unique_id = f"{pod}_{name.lower().replace(' ', '_')}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, pod)},
            "name": f"Rețele Electrice {pod}",
            "manufacturer": "Rețele Electrice România",
        }

    @property
    def _pod_data(self) -> Any:
        pods = (self.coordinator.data or {}).get("pods", {})
        if isinstance(pods, dict):
            return pods.get(self._pod)
        if isinstance(pods, list):
            for pod in pods:
                if isinstance(pod, dict) and str(
                    pod.get("Name") or pod.get("POD__c") or pod.get("POD") or ""
                ) == self._pod:
                    return pod
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"POD": self._pod, "attribution": ATTRIBUTION}


class ConsumptionSensor(ReteleElectriceSensor):
    """Cumulative active consumption when available."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        value = _walk_values(self._pod_data, ("EA", "SUM_EA", "active_consumption"))
        if value is None:
            value = _register_value(self._pod_data, "EA")
        return _to_number(value)


class CurrentPowerSensor(ReteleElectriceSensor):
    """Instantaneous active power when available."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    @property
    def native_value(self) -> float | None:
        value = _walk_values(self._pod_data, ("P_VALUE", "active_power", "power"))
        return _to_number(value)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ReteleElectriceCoordinator = entry.runtime_data.coordinator
    pods = (coordinator.data or {}).get("pods", {})
    if isinstance(pods, list):
        pod_names = [str(item.get("Name") or item.get("POD__c")) for item in pods if isinstance(item, dict)]
    elif isinstance(pods, dict):
        pod_names = [str(name) for name in pods]
    else:
        pod_names = []
    entities: list[SensorEntity] = []
    for pod in pod_names:
        entities.extend((ConsumptionSensor(coordinator, pod, "Consum activ"), CurrentPowerSensor(coordinator, pod, "Putere activă")))
    async_add_entities(entities)
