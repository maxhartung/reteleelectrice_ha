"""Sensors for Rețele Electrice România."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ReteleElectriceCoordinator
from .load_curve import LoadCurveDay, LoadCurveMonth


PARALLEL_UPDATES = 0


def _walk_values(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        # Aura/VF wrappers can change the capitalisation of field names while
        # keeping the same portal payload. Preserve the exact-key fast path,
        # then accept a case-insensitive match for nested responses.
        lowered_keys = {key.lower() for key in keys}
        for actual_key, child in value.items():
            if str(actual_key).lower() in lowered_keys:
                return child
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


def _pod_summary(pod_data: Any) -> dict[str, Any]:
    if isinstance(pod_data, dict) and isinstance(pod_data.get("summary"), dict):
        return pod_data["summary"]
    return pod_data if isinstance(pod_data, dict) else {}


def _data_map(coordinator: Any, key: str) -> dict[str, Any]:
    data = coordinator.data if isinstance(getattr(coordinator, "data", None), dict) else {}
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _rows(value: Any, keys: tuple[str, ...] = ("row", "rows", "XML_Readings", "readings")) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, dict):
                return [nested]
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        for child in value.values():
            nested = _rows(child, keys)
            if nested:
                return nested
    if isinstance(value, list):
        rows = [item for item in value if isinstance(item, dict)]
        if rows:
            return rows
        for child in value:
            nested = _rows(child, keys)
            if nested:
                return nested
    return []


def _reading_datetime(reading: dict[str, Any]) -> datetime | None:
    raw = reading.get("measureDate") or reading.get("date") or reading.get("READING_DATE")
    if not raw:
        return None
    text = str(raw).strip()
    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _archive_readings(coordinator: Any, pod: str) -> list[dict[str, Any]]:
    readings = _rows(_data_map(coordinator, "reading_archive").get(pod))
    return sorted(
        readings,
        key=lambda item: _reading_datetime(item) or datetime.min,
        reverse=True,
    )


def _smart_meter_rows(coordinator: Any, pod: str) -> list[dict[str, Any]]:
    return _rows(_data_map(coordinator, "smart_meter").get(pod))


def _get_energy_value(meters: Any, energy_type: str) -> float | None:
    """Extract a register from both reference and portal response shapes."""
    if isinstance(meters, dict):
        direct = _register_value(meters, energy_type)
        if direct is not None:
            return _to_number(direct)
    for meter in _rows(meters, keys=("meter", "meters", "energyReadingList")):
        register = next(
            (
                meter.get(key)
                for key in ("typeofenergy_measured", "ENERGY_TYPE", "energyType", "register")
                if meter.get(key) is not None
            ),
            None,
        )
        if str(register).upper() == energy_type.upper():
            return _to_number(
                next(
                    (meter.get(key) for key in ("Value", "VALUE", "value", "reading") if key in meter),
                    None,
                )
            )
    return None


def _archive_years(coordinator: Any, pod: str) -> list[int]:
    return sorted(
        {
            parsed.year
            for reading in _archive_readings(coordinator, pod)
            if (parsed := _reading_datetime(reading)) is not None
        },
        reverse=True,
    )[:2]


def _is_prosumer(summary: dict[str, Any]) -> bool:
    producer_flag = summary.get("isProductor__c")
    return bool(
        producer_flag is True
        or str(producer_flag).strip().lower() in {"true", "1", "yes"}
        or str(summary.get("Contract_Type__c", "")).upper() == "PROSUMER"
    )


def _account_info(coordinator: Any) -> dict[str, Any]:
    data = coordinator.data if isinstance(getattr(coordinator, "data", None), dict) else {}
    account = data.get("account_info") or data.get("account")
    return account if isinstance(account, dict) else {}


def _instant_row(instant: Any) -> dict[str, Any]:
    rows = _walk_values(instant, ("dataIstantValueList",))
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


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
    def _instant_data(self) -> Any:
        """Return the reference project's dedicated instant-values entry."""
        data = self.coordinator.data or {}
        instant_values = data.get("instant_values") if isinstance(data, dict) else None
        if isinstance(instant_values, dict):
            return instant_values.get(self._pod)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"POD": self._pod, "attribution": ATTRIBUTION}


class PodInfoSensor(ReteleElectriceSensor):
    """Basic contract and meter metadata for a POD."""

    _attr_icon = "mdi:file-document-outline"

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str) -> None:
        super().__init__(coordinator, pod, "POD")
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_informatii_pod"

    @property
    def native_value(self) -> str:
        return str(_pod_summary(self._pod_data).get("Contract_Type__c") or "Necunoscut")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        summary = _pod_summary(self._pod_data)
        for label, key in (
            ("Stare contract", "Contract_State__c"),
            ("Tip consumator", "Consumer_Type_Account__c"),
            ("Piață", "Market_Type__c"),
            ("Putere absorbită (kW)", "Absorbed_Power_KW__c"),
            ("Nivel tensiune", "Voltage_Level__c"),
            ("Serie contor", "EA_METER_SERIE__c"),
            ("Tip contor", "EA_METER_TYPE__c"),
            ("Tarif", "TARIFF__c"),
        ):
            value = summary.get(key)
            if value not in (None, ""):
                attributes[label] = value
        attributes["Smart meter"] = ReteleElectriceCoordinator._is_smart_meter(summary)
        attributes["Prosumer"] = _is_prosumer(summary)
        return attributes


class AccountInfoSensor(ReteleElectriceSensor):
    """Account details shared by the POD device."""

    _attr_icon = "mdi:account-circle"

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str) -> None:
        super().__init__(coordinator, pod, "Date utilizator")
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_informatii_cont"

    @property
    def native_value(self) -> str:
        return str(_account_info(self.coordinator).get("Name") or "Necunoscut")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        account = _account_info(self.coordinator)
        for label, keys in (
            ("Email", ("Email__c", "Email")),
            ("Telefon", ("Mobile_Phone__c", "MobilePhone")),
            ("Adresă", ("Address__c", "Address")),
            ("Oraș", ("City__c", "City")),
            ("Județ", ("County__c", "County")),
        ):
            value = next((account.get(key) for key in keys if account.get(key)), None)
            if value:
                attributes[label] = value
        return attributes


class ReadingIndexSensor(ReteleElectriceSensor):
    """Latest cumulative index from the reading archive."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: ReteleElectriceCoordinator,
        pod: str,
        register: str,
        name: str,
        key: str,
    ) -> None:
        super().__init__(coordinator, pod, name)
        self._register = register
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_{key}"
        self._attr_icon = "mdi:counter" if register == "EA" else "mdi:solar-power"

    @property
    def native_value(self) -> float | None:
        readings = _archive_readings(self.coordinator, self._pod)
        value = _get_energy_value(readings[0].get("meter", []), self._register) if readings else None
        if value in (None, 0) and self._register == "EA":
            # The archive sometimes publishes a zero placeholder while the
            # instant smart-meter result already contains the current index.
            instant_value = _to_number(_register_value(self._instant_data, "EA"))
            if instant_value is not None and instant_value > 0:
                return instant_value
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        readings = _archive_readings(self.coordinator, self._pod)
        if readings:
            latest = readings[0]
            attributes.update(
                {
                    "Data citire": latest.get("measureDate", ""),
                    "Tip citire": latest.get("typeOfReading", ""),
                    "Serie contor": latest.get("SerialNumber", ""),
                }
            )
            current = _get_energy_value(latest.get("meter", []), self._register)
            if len(readings) > 1:
                previous = _get_energy_value(readings[1].get("meter", []), self._register)
                if current is not None and previous is not None:
                    attributes["Diferență față de citirea anterioară (kWh)"] = round(
                        current - previous, 3
                    )
        return attributes


class ArchiveEnergySensor(ReteleElectriceSensor):
    """Annual energy total derived from the portal reading archive."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: ReteleElectriceCoordinator,
        pod: str,
        year: int,
        register: str,
        name: str,
        key: str,
    ) -> None:
        super().__init__(coordinator, pod, name)
        self._year = year
        self._register = register
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_{key}_{year}"
        self._attr_icon = "mdi:history" if register == "EA" else "mdi:solar-power"

    def _year_readings(self) -> list[dict[str, Any]]:
        return [
            reading
            for reading in _archive_readings(self.coordinator, self._pod)
            if (parsed := _reading_datetime(reading)) is not None and parsed.year == self._year
        ]

    @property
    def native_value(self) -> float | None:
        readings = self._year_readings()
        if not readings:
            return None
        latest = _get_energy_value(readings[0].get("meter", []), self._register)
        oldest = _get_energy_value(readings[-1].get("meter", []), self._register)
        if latest is None:
            return None
        if oldest is not None and len(readings) > 1:
            # A corrected/reset portal index must not become negative energy
            # in Home Assistant's statistics.
            return max(0.0, round(latest - oldest, 3))
        return latest

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        readings = self._year_readings()
        attributes["An"] = self._year
        attributes["Total citiri"] = len(readings)
        attributes["citiri"] = {
            str(reading.get("measureDate", "")): _get_energy_value(
                reading.get("meter", []), self._register
            )
            for reading in readings
        }
        return attributes


class OutageSensor(ReteleElectriceSensor):
    """Current interruption status returned by the portal."""

    _attr_icon = "mdi:flash-alert"

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str) -> None:
        super().__init__(coordinator, pod, "Întreruperi curent")
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_intreruperi_curent"

    @property
    def native_value(self) -> str:
        data = _data_map(self.coordinator, "power_outages").get(self._pod)
        if not isinstance(data, dict):
            return "Fără date"
        status = str(
            _walk_values(data, ("checkInterruzione", "checkInterruption")) or ""
        ).lower()
        if status == "true":
            return "Fără întreruperi"
        if status == "false":
            return "Întrerupere activă"
        return str(
            _walk_values(data, ("esito", "Esito", "result", "Result", "status"))
            or "Necunoscut"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        data = _data_map(self.coordinator, "power_outages").get(self._pod)
        if isinstance(data, dict):
            message = _walk_values(data, ("messaggio", "message"))
            if message:
                attributes["Mesaj"] = message
            attributes["Verificare întreruperi"] = _walk_values(
                data, ("checkInterruzione", "checkInterruption")
            ) or ""
        return attributes


class SmartMeterAggregateSensor(ReteleElectriceSensor):
    """Aggregate smart-meter usage returned for the last 90 days."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: ReteleElectriceCoordinator,
        pod: str,
        register: str,
        name: str,
        key: str,
    ) -> None:
        super().__init__(coordinator, pod, name)
        self._register = register
        self._field = "SUM_EA" if register == "EA" else "SUM_EAP"
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_{key}"
        self._attr_icon = "mdi:chart-line" if register == "EA" else "mdi:solar-power"

    @property
    def native_value(self) -> float | None:
        data = _data_map(self.coordinator, "smart_meter").get(self._pod)
        value = _walk_values(data, (self._field,))
        if value is None:
            rows = _smart_meter_rows(self.coordinator, self._pod)
            value = rows[0].get(self._field) if rows else None
        return _to_number(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        rows = _smart_meter_rows(self.coordinator, self._pod)
        if rows:
            row = rows[0]
            for label, key in (
                ("Perioadă start", "START_DATE"),
                ("Perioadă sfârșit", "END_DATE"),
                ("Contor", "METER"),
            ):
                if row.get(key) not in (None, ""):
                    attributes[label] = row[key]
            peak = _to_number(row.get("MAX_EA" if self._register == "EA" else "MAX_EAP"))
            if peak is not None:
                attributes["Vârf (kWh)"] = peak
        return attributes


class InstantEnergySensor(ReteleElectriceSensor):
    """Cumulative register from the two-step instant smart-meter response."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: ReteleElectriceCoordinator,
        pod: str,
        register: str,
        name: str,
        key: str,
    ) -> None:
        super().__init__(coordinator, pod, name)
        self._register = register
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_{key}"
        self._attr_icon = "mdi:flash" if register == "EA" else "mdi:solar-power"

    @property
    def native_value(self) -> float | None:
        return _to_number(_register_value(self._instant_data, self._register))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        row = _instant_row(self._instant_data)
        for phase, key in (("R", "UR_VALUE"), ("S", "US_VALUE"), ("T", "UT_VALUE")):
            value = _to_number(row.get(key))
            if value is not None:
                attributes[f"Tensiune faza {phase} (V)"] = value
        for phase, key in (("R", "IR_VALUE"), ("S", "IS_VALUE"), ("T", "IT_VALUE")):
            value = _to_number(row.get(key))
            if value is not None:
                attributes[f"Curent faza {phase} (A)"] = value
        power = _to_number(row.get("P_VALUE"))
        if power is not None:
            attributes["Putere activă instantanee (kW)"] = power
        for label, key in (
            ("Data citire", "READING_DATE"),
            ("Ultima actualizare", "LAST_UPDATED"),
            ("Contor", "METER"),
        ):
            if row.get(key) not in (None, ""):
                attributes[label] = row[key]
        return attributes


class SupplierDataSensor(ReteleElectriceSensor):
    """Supplier and technical data returned by the queryPOD proxy."""

    _attr_icon = "mdi:factory"

    def __init__(self, coordinator: ReteleElectriceCoordinator, pod: str) -> None:
        super().__init__(coordinator, pod, "Date furnizor")
        self._attr_unique_id = f"{DOMAIN}_{pod.lower()}_date_furnizor"

    @property
    def native_value(self) -> str:
        data = _data_map(self.coordinator, "supplier_data").get(self._pod)
        value = _walk_values(
            data,
            (
                "cui",
                "CUI",
                "CUI__c",
                "Supplier",
                "supplier",
                "Supplier_Name",
                "supplier_name",
                "furnizor",
                "Name",
            ),
        )
        return str(value or "Fără date")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        data = _data_map(self.coordinator, "supplier_data").get(self._pod)
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (str, int, float, bool)) and value not in (None, ""):
                    attributes[str(key)] = value
        return attributes


class ConsumptionSensor(ReteleElectriceSensor):
    """Cumulative active consumption when available."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        value = _register_value(self._instant_data, "EA")
        if value is None:
            value = _walk_values(self._pod_data, ("EA", "SUM_EA", "active_consumption"))
        if value is None:
            value = _register_value(self._pod_data, "EA")
        return _to_number(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        instant = self._instant_data
        if isinstance(instant, dict):
            rows = instant.get("dataIstantValueList")
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                row = rows[0]
                for phase, key in (("R", "UR_VALUE"), ("S", "US_VALUE"), ("T", "UT_VALUE")):
                    value = _to_number(row.get(key))
                    if value is not None:
                        attributes[f"Tensiune faza {phase} (V)"] = value
                for phase, key in (("R", "IR_VALUE"), ("S", "IS_VALUE"), ("T", "IT_VALUE")):
                    value = _to_number(row.get(key))
                    if value is not None:
                        attributes[f"Curent faza {phase} (A)"] = value
                for label, key in (
                    ("Putere activă instantanee (kW)", "P_VALUE"),
                    ("Data citire", "READING_DATE"),
                    ("Ultima actualizare", "LAST_UPDATED"),
                    ("Contor", "METER"),
                ):
                    value = row.get(key)
                    if value not in (None, ""):
                        attributes[label] = _to_number(value) if key == "P_VALUE" else value
            for register, label in (("ER", "Energie reactivă (kVArh)"), ("EAP", "Energie activă produsă (kWh)")):
                value = _to_number(_register_value(instant, register))
                if value is not None:
                    attributes[label] = value
            if instant.get("Result"):
                attributes["Rezultat"] = instant["Result"]
        return attributes


class CurrentPowerSensor(ReteleElectriceSensor):
    """Instantaneous active power when available."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    @property
    def native_value(self) -> float | None:
        value = _walk_values(
            self._instant_data,
            (
                "P_VALUE",
                "POWER_VALUE",
                "active_power",
                "activePower",
                "power",
            ),
        )
        if value is None:
            value = _walk_values(
                self._pod_data,
                (
                    "P_VALUE",
                    "POWER_VALUE",
                    "active_power",
                    "activePower",
                    "power",
                ),
            )
        return _to_number(value)


class LoadCurveSensor(ReteleElectriceSensor):
    """Base class for values derived from the monthly load curve."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def _curve(self) -> LoadCurveMonth | None:
        value = self._pod_data
        if isinstance(value, dict) and isinstance(value.get("load_curve"), LoadCurveMonth):
            return value["load_curve"]
        return None

    @property
    def _latest_day(self) -> LoadCurveDay | None:
        return self._curve.latest_day if self._curve else None

    @property
    def _latest_hour(self) -> tuple[int, float] | None:
        latest_day = self._latest_day
        if not latest_day:
            return None
        for hour, value in reversed(tuple(enumerate(latest_day.hourly_totals("EA")))):
            if value is not None:
                return hour, value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        curve = self._curve
        latest_day = self._latest_day
        if curve and latest_day:
            attributes.update(
                {
                    "curve_date": latest_day.day.isoformat(),
                    "curve_frequency_minutes": latest_day.frequency_minutes,
                    "daily_consumption": curve.daily_totals("EA"),
                    "hourly_consumption": {
                        f"{hour:02d}:00": value
                        for hour, value in enumerate(latest_day.hourly_totals("EA"))
                        if value is not None
                    },
                }
            )
            latest_hour = self._latest_hour
            if latest_hour:
                attributes["curve_hour"] = f"{latest_hour[0]:02d}:00"
        return attributes


class DailyLoadCurveSensor(LoadCurveSensor):
    """Total active consumption for the latest day supplied by the portal."""

    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        latest_day = self._latest_day
        return latest_day.total("EA") if latest_day else None


class HourlyLoadCurveSensor(LoadCurveSensor):
    """The latest available one-hour active-consumption bucket."""

    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        latest_hour = self._latest_hour
        return latest_hour[1] if latest_hour else None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ReteleElectriceCoordinator = entry.runtime_data.coordinator
    data = coordinator.data or {}
    pods = data.get("pods", {}) if isinstance(data, dict) else {}
    if isinstance(pods, list):
        pod_items = [
            (
                str(item.get("Name") or item.get("POD__c")),
                {"summary": item},
            )
            for item in pods
            if isinstance(item, dict) and (item.get("Name") or item.get("POD__c"))
        ]
    elif isinstance(pods, dict):
        pod_items = [(str(name), value) for name, value in pods.items()]
    else:
        pod_items = []
    entities: list[SensorEntity] = []
    for pod, pod_data in pod_items:
        summary = _pod_summary(pod_data)
        is_smart = ReteleElectriceCoordinator._is_smart_meter(summary)
        is_prosumer = _is_prosumer(summary)
        entities.extend(
            (
                PodInfoSensor(coordinator, pod),
                AccountInfoSensor(coordinator, pod),
                OutageSensor(coordinator, pod),
                ReadingIndexSensor(
                    coordinator,
                    pod,
                    "EA",
                    "Index citire consum",
                    "index_citire_consum",
                ),
                ConsumptionSensor(coordinator, pod, "Consum activ"),
                CurrentPowerSensor(coordinator, pod, "Putere activă"),
                DailyLoadCurveSensor(coordinator, pod, "Consum zilnic (curbă)"),
                HourlyLoadCurveSensor(coordinator, pod, "Consum ultima oră (curbă)"),
                SupplierDataSensor(coordinator, pod),
            )
        )
        if is_prosumer:
            entities.append(
                ReadingIndexSensor(
                    coordinator,
                    pod,
                    "EAP",
                    "Index citire producție",
                    "index_citire_productie",
                )
            )
        for year in _archive_years(coordinator, pod):
            entities.append(
                ArchiveEnergySensor(
                    coordinator,
                    pod,
                    year,
                    "EA",
                    f"{year} → Energie consumată",
                    "arhiva_energie_consumata",
                )
            )
            if is_prosumer:
                entities.append(
                    ArchiveEnergySensor(
                        coordinator,
                        pod,
                        year,
                        "EAP",
                        f"{year} → Energie produsă",
                        "arhiva_energie_produsa",
                    )
                )
        if is_smart:
            entities.extend(
                (
                    SmartMeterAggregateSensor(
                        coordinator,
                        pod,
                        "EA",
                        "Smart Meter Consum",
                        "smart_meter_consum",
                    ),
                    InstantEnergySensor(
                        coordinator,
                        pod,
                        "EA",
                        "Valoare instantanee consum",
                        "valoare_instantanee_consum",
                    ),
                )
            )
            if is_prosumer:
                entities.extend(
                    (
                        SmartMeterAggregateSensor(
                            coordinator,
                            pod,
                            "EAP",
                            "Smart Meter Producție",
                            "smart_meter_productie",
                        ),
                        InstantEnergySensor(
                            coordinator,
                            pod,
                            "EAP",
                            "Valoare instantanee producție",
                            "valoare_instantanee_productie",
                        ),
                    )
                )
    async_add_entities(entities)
