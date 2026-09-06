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


def parse_meter(value: Any) -> dict[str, Any] | None:
    row = {key.upper(): item for key, item in instant_row(value).items()}
    if not row:
        return None
    voltage = number(row.get("UR_VALUE"))
    current = number(row.get("IR_VALUE"))
    timestamp = website_timestamp(row.get("LAST_UPDATED"))
    if voltage is None and current is None and timestamp is None:
        return None
    return {
        "voltage": voltage,
        "current": current,
        "estimated_power": round(voltage * current / 1000, 3)
        if voltage is not None and current is not None else None,
        "last_update": timestamp.isoformat() if timestamp else None,
    }
