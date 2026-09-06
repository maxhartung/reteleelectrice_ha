"""Parse only the website's instantaneous smart-meter fields."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any
from zoneinfo import ZoneInfo

PORTAL_TIMEZONE = ZoneInfo("Europe/Bucharest")


def instant_row(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() == "dataistantvaluelist" and isinstance(child, list):
                return next((row for row in child if isinstance(row, dict)), {})
        for child in value.values():
            if row := instant_row(child):
                return row
    elif isinstance(value, list):
        for child in value:
            if row := instant_row(child):
                return row
    return {}


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in text:
        if "." in text and text.rfind(".") > text.rfind(","):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) and result >= 0 else None


def website_timestamp(value: Any) -> datetime | None:
    """Use the website update time; never substitute the HA polling time."""
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=PORTAL_TIMEZONE)


def energy_import(row: dict[str, Any]) -> float | None:
    """Read the cumulative active-import register, never derive kWh from V×A."""
    readings = row.get("ENERGYREADINGLIST", [])
    if isinstance(readings, list):
        for reading in readings:
            if not isinstance(reading, dict):
                continue
            fields = {key.upper(): value for key, value in reading.items()}
            if str(fields.get("ENERGY_TYPE", "")).upper() == "EA":
                return number(fields.get("VALUE"))
    return None


def parse_meter(value: Any) -> dict[str, Any] | None:
    row = {key.upper(): item for key, item in instant_row(value).items()}
    if not row:
        return None
    voltage = number(row.get("UR_VALUE"))
    current = number(row.get("IR_VALUE"))
    timestamp = website_timestamp(row.get("LAST_UPDATED"))
    energy = energy_import(row)
    # Keep each snapshot coherent. A partial response must not erase a valid
    # reading or combine measurements taken at different website timestamps.
    if voltage is None or current is None or timestamp is None or energy is None:
        return None
    return {
        "voltage": voltage,
        "current": current,
        "energy_import": energy,
        "estimated_power": round(voltage * current / 1000, 3)
        if voltage is not None and current is not None else None,
        "last_update": timestamp.isoformat() if timestamp else None,
    }
