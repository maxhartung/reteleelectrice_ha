"""Coordinated portal polling."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AuthenticationError, PortalError, ReteleElectriceClient
from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN

LOGGER = logging.getLogger(DOMAIN)


class ReteleElectriceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch account and POD data once for all entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ReteleElectriceClient,
    ) -> None:
        interval = entry.options.get(
            CONF_UPDATE_INTERVAL,
            entry.data.get(CONF_UPDATE_INTERVAL, int(DEFAULT_UPDATE_INTERVAL.total_seconds())),
        )
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=int(interval)),
            always_update=False,
        )
        self.entry = entry
        self.client = client

    async def _async_setup(self) -> None:
        """Authenticate once before the first coordinated data refresh."""
        try:
            await self.client.async_login()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except PortalError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            raw_pods = await self.client.async_get_pods()
            pod_list = self._normalise_pods(raw_pods)
            pod_data: dict[str, Any] = {}

            account_info: Any = None
            try:
                account_info = await self.client.async_get_account_info()
            except AuthenticationError:
                raise
            except PortalError as err:
                LOGGER.warning("Account metadata was unavailable: %s", err)

            contact_info: Any = None
            try:
                contact_info = await self.client.async_get_contact_info()
            except AuthenticationError:
                raise
            except PortalError as err:
                LOGGER.warning("Contact metadata was unavailable: %s", err)

            cnp = ""
            if isinstance(account_info, dict):
                cnp = str(account_info.get("CNP__c") or account_info.get("Fiscal_Code__c") or "")

            now = dt_util.now()
            instant_values: dict[str, Any] = {}
            reading_archive: dict[str, Any] = {}
            power_outages: dict[str, Any] = {}
            smart_meter: dict[str, Any] = {}
            supplier_data: dict[str, Any] = {}
            pod_reading_details: dict[str, Any] = {}
            for summary in pod_list:
                pod_name = self._pod_name(summary)
                if not pod_name:
                    continue
                current: dict[str, Any] = {"summary": summary}
                try:
                    current["details"] = await self.client.async_get_pod_details(pod_name)
                except AuthenticationError:
                    raise
                except PortalError as err:
                    LOGGER.warning("POD details unavailable for %s: %s", pod_name, err)

                try:
                    pod_reading_details[pod_name] = (
                        await self.client.async_get_reading_archive_pod_details(pod_name)
                    )
                except AuthenticationError:
                    raise
                except PortalError as err:
                    LOGGER.warning("Reading metadata unavailable for %s: %s", pod_name, err)

                try:
                    archive = await self.client.async_get_reading_archive(pod_name, cnp=cnp)
                    reading_archive[pod_name] = archive
                    current["reading_archive"] = archive
                except AuthenticationError:
                    raise
                except PortalError as err:
                    LOGGER.warning("Reading archive unavailable for %s: %s", pod_name, err)

                try:
                    outage = await self.client.async_get_power_outages(pod_name)
                    power_outages[pod_name] = outage
                    current["power_outages"] = outage
                except AuthenticationError:
                    raise
                except PortalError as err:
                    LOGGER.warning("Power-outage data unavailable for %s: %s", pod_name, err)

                if self._is_smart_meter(summary):
                    try:
                        historical = await self.client.async_get_smart_meter_data(
                            pod_name, cnp=cnp
                        )
                        smart_meter[pod_name] = historical
                        current["smart_meter"] = historical
                    except AuthenticationError:
                        raise
                    except PortalError as err:
                        LOGGER.warning("Smart-meter history unavailable for %s: %s", pod_name, err)
                    try:
                        instant = await self.client.async_get_instant_values(pod_name, cnp)
                        current["instant_values"] = instant
                        # Keep the old key for compatibility with existing
                        # entities and callers.
                        current["smart_meter_current"] = instant
                        instant_values[pod_name] = instant
                    except AuthenticationError:
                        raise
                    except PortalError as err:
                        LOGGER.warning("Instant smart-meter values unavailable for %s: %s", pod_name, err)

                try:
                    supplier = await self.client.async_get_supplier_data(pod_name)
                    supplier_data[pod_name] = supplier
                    current["supplier_data"] = supplier
                except AuthenticationError:
                    raise
                except PortalError as err:
                    LOGGER.warning("Supplier data unavailable for %s: %s", pod_name, err)

                try:
                    current["load_curve"] = await self.client.async_get_load_curve(
                        pod_name,
                        now.year,
                        now.month,
                    )
                except AuthenticationError:
                    raise
                except PortalError as err:
                    LOGGER.warning("Load curve unavailable for %s: %s", pod_name, err)
                pod_data[pod_name] = current

            return {
                "account": account_info,
                "account_info": account_info,
                "contact_info": contact_info,
                "pods": pod_data,
                "pod_reading_details": pod_reading_details,
                "reading_archive": reading_archive,
                "power_outages": power_outages,
                "smart_meter": smart_meter,
                "instant_values": instant_values,
                "supplier_data": supplier_data,
            }
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("Authentication expired") from err
        except PortalError as err:
            raise UpdateFailed(str(err)) from err

    async def async_request_instant_refresh(self, pod_name: str) -> None:
        """Run the portal's two-step smart-meter refresh for one POD."""
        data = self.data
        if not isinstance(data, dict):
            await self.async_request_refresh()
            data = self.data
        if not isinstance(data, dict):
            raise UpdateFailed("No coordinator data is available")

        account_info = data.get("account")
        cnp = ""
        if isinstance(account_info, dict):
            cnp = str(account_info.get("CNP__c") or account_info.get("Fiscal_Code__c") or "")
        instant_values = await self.client.async_get_instant_values(pod_name, cnp)

        pods = data.get("pods")
        if not isinstance(pods, dict) or pod_name not in pods:
            return
        updated_data = dict(data)
        updated_pods = dict(pods)
        updated_pod = dict(updated_pods[pod_name])
        existing_instant = data.get("instant_values")
        updated_instant = dict(existing_instant) if isinstance(existing_instant, dict) else {}
        updated_instant[pod_name] = instant_values
        updated_data["instant_values"] = updated_instant
        updated_pod["instant_values"] = instant_values
        updated_pod["smart_meter_current"] = instant_values
        updated_pods[pod_name] = updated_pod
        updated_data["pods"] = updated_pods
        self.async_set_updated_data(updated_data)

    @staticmethod
    def _normalise_pods(raw_pods: Any) -> list[dict[str, Any]]:
        if isinstance(raw_pods, list):
            return [item for item in raw_pods if isinstance(item, dict)]
        if isinstance(raw_pods, dict):
            for key in ("records", "rows", "PODs", "pods", "data"):
                nested = raw_pods.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]
            return [raw_pods]
        return []

    @staticmethod
    def _pod_name(summary: dict[str, Any]) -> str:
        for key in ("Name", "POD__c", "POD", "pod"):
            value = summary.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _is_smart_meter(summary: dict[str, Any]) -> bool:
        """Return whether a POD supports the two-step instant-meter API."""
        keys = ("Smart_meter__c", "IsSmartMeter__c", "smart_meter", "is_smart_meter")
        present = [key for key in keys if key in summary]
        if not present:
            # Some portal responses omit the capability flag. In that case,
            # try the endpoint and let the portal report unavailable data.
            return True
        return any(
            summary.get(key) is True
            or str(summary.get(key, "")).strip().lower() in {"true", "1", "yes"}
            for key in present
        )
