"""Smart-meter display values and cumulative energy for the Energy dashboard."""

from __future__ import annotations

from datetime import datetime
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfPower, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ReteleElectriceCoordinator

PARALLEL_UPDATES = 0
DESCRIPTIONS = (
    SensorEntityDescription(key="voltage", name="Voltage", device_class=SensorDeviceClass.VOLTAGE,
                            native_unit_of_measurement=UnitOfElectricPotential.VOLT, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="current", name="Current", device_class=SensorDeviceClass.CURRENT,
                            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="estimated_power", name="Estimated power", device_class=SensorDeviceClass.POWER,
                            native_unit_of_measurement=UnitOfPower.KILO_WATT, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="energy_import", name="Grid energy imported", device_class=SensorDeviceClass.ENERGY,
                            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, state_class=SensorStateClass.TOTAL_INCREASING),
    SensorEntityDescription(key="last_update", name="Website last update", device_class=SensorDeviceClass.TIMESTAMP),
)
# Preserve existing voltage/current entity identities on upgrade.
UNIQUE_SUFFIXES = {"voltage": "tensiune_faza_r", "current": "curent_faza_r",
                   "estimated_power": "estimated_power", "last_update": "website_last_update", "energy_import": "energy_import"}


class SmartMeterSensor(CoordinatorEntity[ReteleElectriceCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._pod = pod
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_{UNIQUE_SUFFIXES[description.key]}"
        self._attr_device_info = {"identifiers": {(DOMAIN, pod)}, "name": f"Rețele Electrice {pod}",
                                  "manufacturer": "Rețele Electrice România"}

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get("pods", {}).get(self._pod, {}).get(self.entity_description.key)
        return datetime.fromisoformat(value) if value and self.entity_description.key == "last_update" else value

    @property
    def extra_state_attributes(self):
        attributes = {"attribution": ATTRIBUTION}
        if self.entity_description.key == "estimated_power":
            attributes.update({"assumed_power_factor": 1, "calculation": "Voltage × Current / 1000",
                               "note": "Estimate for phase R; actual active power depends on power factor."})
        return attributes


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(SmartMeterSensor(coordinator, pod, description)
                       for pod in (coordinator.data or {}).get("pods", {}) for description in DESCRIPTIONS)
