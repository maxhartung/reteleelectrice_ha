"""Coordinated portal polling."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
            update_interval=timedelta(seconds=int(interval)),
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            raw_pods = await self.client.async_get_pods()
            pod_list = self._normalise_pods(raw_pods)
            pod_data: dict[str, Any] = {}

            account_info: Any = None
            try:
                account_info = await self.client.async_get_account_info()
            except PortalError:
                LOGGER.debug("Account metadata was unavailable", exc_info=True)

            cnp = ""
            if isinstance(account_info, dict):
                cnp = str(account_info.get("CNP__c") or account_info.get("Fiscal_Code__c") or "")

            now = dt_util.now()
            for summary in pod_list:
                pod_name = self._pod_name(summary)
                if not pod_name:
                    continue
                current: dict[str, Any] = {"summary": summary}
                try:
                    current["details"] = await self.client.async_get_pod_details(pod_name)
                except PortalError:
                    LOGGER.debug("POD details unavailable for %s", pod_name, exc_info=True)
                try:
                    current["smart_meter_current"] = await self.client.async_get_smart_meter_current(pod_name, cnp)
                except PortalError:
                    LOGGER.debug("Smart-meter current values unavailable for %s", pod_name, exc_info=True)
                try:
                    current["load_curve"] = await self.client.async_get_load_curve(
                        pod_name,
                        now.year,
                        now.month,
                    )
                except PortalError:
                    LOGGER.debug("Load curve unavailable for %s", pod_name, exc_info=True)
                pod_data[pod_name] = current

            return {"account": account_info, "pods": pod_data}
        except AuthenticationError as err:
            raise UpdateFailed("Authentication expired") from err
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
