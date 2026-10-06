"""Diagnostics with sensitive values removed."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact_data import async_redact_data

from .const import CONF_EMAIL, CONF_PASSWORD

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    """Return diagnostics without credentials or raw personal details."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "coordinator", None)
    data = getattr(coordinator, "data", {}) or {}
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "coordinator_keys": list(data.keys()) if isinstance(data, dict) else [],
        "polling": coordinator.diagnostics if coordinator is not None else {"poll_status": "not_loaded"},
    }
