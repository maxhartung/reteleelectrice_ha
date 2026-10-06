"""Poll smart-meter results and automatically submit quota-limited requests."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthenticationError, PortalError, ReteleElectriceClient, SessionExpiredError
from .consumption_request import ConsumptionRequestState
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .meter import parse_meter
from .storage import ConfirmedStore

LOGGER = logging.getLogger(__name__)


class ReteleElectriceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep the smart-meter values and persist quota before every submission."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ReteleElectriceClient) -> None:
        super().__init__(hass, logger=LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=DEFAULT_UPDATE_INTERVAL, always_update=False)
        self.entry = entry
        self.client = client
        self._store = ConfirmedStore(hass, 1, f"{DOMAIN}.{entry.entry_id}.meter", atomic_writes=True)
        self._requests = ConsumptionRequestState()
        self._meters: dict[str, Any] = {}
        self._params: dict[str, list[str]] = {}
        self._last_requests: dict[str, str] = {}
        self._poll_status = "not_polled"
        self._last_poll: str | None = None
        self._meter_status: dict[str, str] = {}

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Expose polling outcomes without account IDs, cookies, or payloads."""
        return {
            "poll_status": self._poll_status,
            "last_poll": self._last_poll,
            "portal_session_initialized": self.client.is_logged_in,
            "cached_meter_count": len(self._meters),
            "meter_statuses": list(self._meter_status.values()),
            "request_attempt_times": self._requests.as_list(),
        }

    async def _save(self) -> None:
        await self._store.async_save({"attempts": self._requests.as_list(), "meters": self._meters,
                                      "last_requests": self._last_requests})

    async def _async_setup(self) -> None:
        saved = await self._store.async_load() or {}
        self._requests = ConsumptionRequestState(saved.get("attempts", []))
        self._meters = saved.get("meters", {})
        self._last_requests = saved.get("last_requests", {})
        try:
            await self.client.async_login()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except (PortalError, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> dict[str, Any]:
        self._last_poll = datetime.now(timezone.utc).isoformat()
        self._poll_status = "polling"
        self._meter_status = {}
        try:
            pods = self._normalise_pods(await self.client.async_get_pods())
            if not any(self._pod_name(pod) for pod in pods):
                # A guest/expired Salesforce session can return SUCCESS with
                # no PODs instead of an explicit authentication error. Renew
                # once before failing; otherwise every later poll reuses the
                # same stale session until Home Assistant is restarted.
                LOGGER.warning("Portal returned no PODs; renewing the session once")
                await self.client.async_relogin()
                pods = self._normalise_pods(await self.client.async_get_pods())
                if not any(self._pod_name(pod) for pod in pods):
                    raise UpdateFailed("Portal returned no PODs after session renewal; retaining the last meter readings")
            result = {}
            for summary in sorted(pods, key=lambda item: self._last_requests.get(self._pod_name(item), "")):
                pod = self._pod_name(summary)
                if not pod or not self._is_smart_meter(summary):
                    continue
                result[pod] = dict(self._meters.get(pod, {}))
                self._meter_status[pod] = "cached" if result[pod] else "no_reading"
                try:
                    if pod not in self._params:
                        self._params[pod] = await self.client.async_meter_params(pod)
                    params = self._params[pod]
                    now = datetime.now(timezone.utc)
                    if self._requests.can_request(now):
                        self._requests.mark_requested(now)
                        self._last_requests[pod] = now.isoformat()
                        # Save before network I/O. Timeouts and restarts must not permit duplicates.
                        await self._save()
                        try:
                            await self.client.async_request_meter_data(params)
                        except AuthenticationError:
                            # The submission may have reached the server. Renew the
                            # session, but never replay it or release its reservation.
                            await self.client.async_relogin()
                        except (PortalError, aiohttp.ClientError, TimeoutError) as err:
                            LOGGER.warning("Meter request failed; attempt remains counted: %s", type(err).__name__)
                    reading = parse_meter(await self.client.async_read_meter_data(params))
                    if reading is not None:
                        previous = self._meters.get(pod, {})
                        old_time = previous.get("last_update")
                        new_time = reading.get("last_update")
                        if not old_time or (new_time and datetime.fromisoformat(new_time) >= datetime.fromisoformat(old_time)):
                            result[pod] = reading
                            self._meters[pod] = reading
                            self._meter_status[pod] = "reading_received"
                        else:
                            self._meter_status[pod] = "older_reading_ignored"
                    else:
                        self._meter_status[pod] = "no_complete_reading"
                        LOGGER.warning("Portal returned no meter values; retaining the last reading")
                except AuthenticationError:
                    raise
                except (PortalError, aiohttp.ClientError, TimeoutError) as err:
                    self._meter_status[pod] = type(err).__name__
                    LOGGER.warning("Smart-meter values unavailable: %s", type(err).__name__)
            await self._save()
            self._poll_status = "ok"
            return {"pods": result}
        except SessionExpiredError as err:
            self._poll_status = "session_expired"
            raise UpdateFailed("Portal rejected the renewed session; will retry later") from err
        except AuthenticationError as err:
            self._poll_status = "authentication_failed"
            raise ConfigEntryAuthFailed("Authentication expired") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            self._poll_status = "connection_failed"
            raise UpdateFailed("Could not connect to the portal") from err
        except PortalError as err:
            self._poll_status = "portal_failed"
            raise UpdateFailed(str(err)) from err
        except OSError:
            self._poll_status = "storage_failed"
            raise

    @staticmethod
    def _normalise_pods(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            for key in ("records", "rows", "PODs", "pods", "data"):
                if isinstance(raw.get(key), list):
                    return [item for item in raw[key] if isinstance(item, dict)]
            return [raw]
        return []

    @staticmethod
    def _pod_name(summary: dict[str, Any]) -> str:
        return str(next((summary[key] for key in ("Name", "POD__c", "POD", "pod") if summary.get(key)), ""))

    @staticmethod
    def _is_smart_meter(summary: dict[str, Any]) -> bool:
        keys = [key for key in ("Smart_meter__c", "IsSmartMeter__c", "smart_meter", "is_smart_meter") if key in summary]
        return not keys or any(str(summary[key]).lower() in {"true", "1", "yes"} for key in keys)
