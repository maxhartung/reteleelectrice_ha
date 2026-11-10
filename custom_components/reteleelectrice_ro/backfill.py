"""Persist detailed curves and replay idempotent external energy statistics."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics

from .api import PortalError
from .const import CONF_IMPORT_PRICE, DEFAULT_IMPORT_PRICE, DOMAIN
from .load_curve import PORTAL_ZONE, hourly_energy, merge_load_row, parse_load_data
from .storage import ConfirmedStore

LOGGER = logging.getLogger(__name__)


class DetailedBackfill:
    """Fetch a rolling month through yesterday, retrying delayed publication."""

    def __init__(self, hass, entry, client):
        self.hass = hass
        self.client = client
        self.import_price = entry.options.get(CONF_IMPORT_PRICE, DEFAULT_IMPORT_PRICE)
        self.store = ConfirmedStore(hass, 1, f'{DOMAIN}.{entry.entry_id}.curves', atomic_writes=True)
        self.data = {}
        self._next_attempt = {}
        self.status = {}

    async def async_load(self):
        self.data = await self.store.async_load() or {}
        # Replay on startup too: a crash after saving but before recorder commit
        # must not permanently lose historical data.
        for pod in self.data:
            self._import_cached(pod)

    async def async_update(self, pod, params):
        now = datetime.now(PORTAL_ZONE)
        if now < self._next_attempt.get(pod, now):
            return
        self._next_attempt[pod] = now + timedelta(hours=6)
        yesterday = now.date() - timedelta(days=1)
        start = yesterday - timedelta(days=30)
        try:
            fresh = parse_load_data(await self.client.async_get_detailed_load(params, start, yesterday))
            days = dict(self.data.get(pod, {}))
            # Merge channels independently. Missing/empty results never erase
            # previously downloaded readings.
            for day, channels in fresh.items():
                if start.isoformat() <= day <= yesterday.isoformat():
                    target = dict(days.get(day, {}))
                    for channel, row in channels.items():
                        if any(value is not None for value in row['values']):
                            target[channel] = merge_load_row(day, target.get(channel), row)
                    days[day] = target
            updated = dict(self.data, **{pod: days})
            await self.store.async_save(updated)
            self.data = updated
            self.status[pod] = 'updated' if yesterday.isoformat() in fresh else 'awaiting_publication'
        except (PortalError, aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, OSError) as err:
            self.status[pod] = type(err).__name__
            LOGGER.warning('Detailed energy backfill unavailable: %s', type(err).__name__)
        # Full replay also repairs corrected historical values and recorder loss.
        # Statistic timestamps are upserted, never added to existing totals.
        self._import_cached(pod)

    def _import_cached(self, pod):
        for channel, label in (('WI', 'import_cost'), ('WI', 'import'), ('WE', 'export')):
            try:
                self._import_channel(pod, channel, label)
            except (HomeAssistantError, ValueError, KeyError, TypeError) as err:
                self.status[pod] = "statistics_failed"
                LOGGER.warning("Detailed energy statistics unavailable: %s", type(err).__name__)

    def _import_channel(self, pod, channel, label):
        statistics = hourly_energy(self.data.get(pod, {}), channel)
        is_cost = label == 'import_cost'
        if is_cost:
            statistics = [dict(stat, sum=round(stat['sum'] * self.import_price, 6)) for stat in statistics]
        if statistics:
            async_add_external_statistics(self.hass, {
                'statistic_id': f'{DOMAIN}:{pod.lower()}_detailed_{label}',
                'source': DOMAIN,
                'name': f'{pod} estimated grid import cost' if is_cost else f'{pod} detailed grid energy {label}',
                'unit_of_measurement': 'RON' if is_cost else 'kWh',
                'unit_class': None if is_cost else 'energy',
                'has_sum': True,
                'mean_type': StatisticMeanType.NONE,
            }, statistics)
