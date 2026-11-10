"""Exercise backfill persistence and recorder imports at framework boundaries."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_load_curve import curve, payload

ROOT = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro'


class PortalError(Exception):
    pass


class BackfillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tree = ast.parse((ROOT / 'backfill.py').read_text())
        tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        self.store = SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
        self.import_stats = Mock()
        ns = dict(datetime=datetime, timedelta=timedelta, PORTAL_ZONE=curve.PORTAL_ZONE,
                  parse_load_data=curve.parse_load_data, hourly_energy=curve.hourly_energy,
                  merge_load_row=curve.merge_load_row, HomeAssistantError=RuntimeError,
                  ConfirmedStore=lambda *a, **kw: self.store, DOMAIN='reteleelectrice_ro',
                  PortalError=PortalError, aiohttp=SimpleNamespace(ClientError=ConnectionError),
                  LOGGER=logging.getLogger(__name__), StatisticMeanType=SimpleNamespace(NONE=0),
                  async_add_external_statistics=self.import_stats)
        exec(compile(tree, 'backfill.py', 'exec'), ns)
        self.client = SimpleNamespace(async_get_detailed_load=AsyncMock())
        self.backfill = ns['DetailedBackfill'](None, SimpleNamespace(entry_id='test'), self.client)
        await self.backfill.async_load()
        self.yesterday = datetime.now(curve.PORTAL_ZONE).date() - timedelta(days=1)
        self.client.async_get_detailed_load.return_value = payload(day=self.yesterday.isoformat())

    async def test_date_window_excludes_today_and_refresh_is_throttled(self):
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.client.async_get_detailed_load.assert_awaited_once_with(['id', '', 'POD'], self.yesterday-timedelta(days=30), self.yesterday)
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.client.async_get_detailed_load.assert_awaited_once()
        self.assertEqual(self.import_stats.call_args.args[1]['statistic_id'], 'reteleelectrice_ro:pod_detailed_import')

    async def test_restart_replays_same_sums_and_keeps_raw_intervals(self):
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        before = deepcopy(self.import_stats.call_args.args[2])
        saved = deepcopy(self.store.async_save.call_args.args[0])
        self.assertEqual(len(saved['POD'][self.yesterday.isoformat()]['WI']['values']), 96)
        self.backfill.data = saved
        self.backfill._next_attempt.clear()
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.assertEqual(self.import_stats.call_args.args[2], before)

    async def test_failed_persistence_does_not_import_uncommitted_data(self):
        self.store.async_save.side_effect = OSError()
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.assertEqual(self.backfill.data, {})
        self.import_stats.assert_not_called()

    async def test_missing_response_preserves_cached_readings_and_replays(self):
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        saved = deepcopy(self.backfill.data)
        self.backfill._next_attempt.clear()
        self.client.async_get_detailed_load.side_effect = PortalError()
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.assertEqual(self.backfill.data, saved)
        self.assertEqual(self.import_stats.call_count, 2)


    async def test_short_response_does_not_erase_complete_persisted_day(self):
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        saved = deepcopy(self.backfill.data)
        first_stats = deepcopy(self.import_stats.call_args.args[2])
        self.backfill._next_attempt.clear()
        self.client.async_get_detailed_load.return_value = payload(day=self.yesterday.isoformat(), values=['9,0'] * 92)
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.assertEqual(self.backfill.data, saved)
        self.assertEqual(self.import_stats.call_args.args[2], first_stats)
        self.assertEqual(self.store.async_save.call_args.args[0], saved)

    async def test_statistics_failure_does_not_escape_or_erase_download(self):
        self.import_stats.side_effect = RuntimeError('recorder unavailable')
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        self.assertIn(self.yesterday.isoformat(), self.backfill.data['POD'])
        self.assertEqual(self.backfill.status['POD'], 'statistics_failed')

    async def test_current_recorder_metadata_and_timestamps(self):
        await self.backfill.async_update('POD', ['id', '', 'POD'])
        _, metadata, statistics = self.import_stats.call_args.args
        self.assertEqual(metadata['unit_class'], 'energy')
        self.assertEqual(metadata['mean_type'], 0)
        self.assertEqual(metadata['unit_of_measurement'], 'kWh')
        self.assertTrue(metadata['has_sum'])
        self.assertEqual(metadata['source'], metadata['statistic_id'].split(':')[0])
        for stat in statistics:
            self.assertIsNotNone(stat['start'].utcoffset())
            self.assertEqual((stat['start'].minute, stat['start'].second, stat['start'].microsecond), (0, 0, 0))
