"""Helpers for deriving average active power from cumulative meter readings."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _instant_row(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        rows = value.get("dataIstantValueList")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        for child in value.values():
            row = _instant_row(child)
            if row:
                return row
    elif isinstance(value, list):
        for child in value:
            row = _instant_row(child)
            if row:
                return row
    return {}


def _register_value(value: Any, register: str) -> Any:
    """Find a typed cumulative register in the portal response."""
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


def instant_active_energy(value: Any) -> float | None:
    """Return the active import register (OBIS 1.8.0 / EA) in kWh."""
    return _to_number(_register_value(value, "EA"))


def instant_timestamp(value: Any, fallback: datetime) -> datetime:
    """Return the meter timestamp, falling back to the retrieval time."""
    row = _instant_row(value)
    raw = row.get("LAST_UPDATED") or row.get("READING_DATE") or row.get("readingDate")
    if isinstance(raw, datetime):
        return raw
    if raw:
        text = str(raw).strip()
        for pattern in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
            except ValueError:
                continue
            return parsed.replace(tzinfo=fallback.tzinfo)
    return fallback


def calculate_average_active_power(
    previous: tuple[float, datetime] | None,
    current: tuple[float, datetime],
) -> float | None:
    """Calculate kW from two cumulative active-energy readings."""
    if previous is None:
        return None
    previous_energy, previous_time = previous
    current_energy, current_time = current
    elapsed_hours = (current_time - previous_time).total_seconds() / 3600
    energy_delta = current_energy - previous_energy
    if elapsed_hours <= 0 or energy_delta < 0:
        return None
    return round(energy_delta / elapsed_hours, 3)
