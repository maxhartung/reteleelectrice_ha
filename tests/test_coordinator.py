"""Exercise the actual coordinator against fake network and storage boundaries."""
from __future__ import annotations
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoordinatorBase:
    def __class_getitem__(cls, key):
        return cls

    def __init__(self, *args, **kwargs):
        self.data = None


class PortalError(Exception):
    pass


class AuthError(PortalError):
    pass


class Store:
    def __init__(self, *args):
        self.saved = None
        self.events = []
        self.fail = False

    async def async_save(self, data):
        self.events.append('save')
        if self.fail:
            raise OSError('disk unavailable')
        self.saved = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.saved)


def coordinator_class():
    tree = ast.parse((ROOT / 'coordinator.py').read_text())
    # Stub only HA's framework boundary; execute the real coordinator unchanged.
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom)) or
                 isinstance(n, ast.ImportFrom) and n.module == '__future__']
    namespace = dict(Any=object, DataUpdateCoordinator=CoordinatorBase, Store=Store, datetime=datetime,
                     timezone=timezone, logging=logging, DOMAIN='reteleelectrice_ro',
                     DEFAULT_UPDATE_INTERVAL=timedelta(minutes=5), PortalError=PortalError,
                     AuthenticationError=AuthError, ConfigEntryAuthFailed=AuthError,
                     UpdateFailed=PortalError, ConsumptionRequestState=load('consumption_request').ConsumptionRequestState,
                     parse_meter=load('meter').parse_meter)
    exec(compile(tree, 'coordinator.py', 'exec'), namespace)
    return namespace['ReteleElectriceCoordinator']


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncMock()
        self.client.async_get_pods.return_value = [{'Name': 'POD', 'Smart_meter__c': True}]
        self.client.async_meter_params.return_value = ['', '', 'POD']
        self.client.async_read_meter_data.return_value = {'dataIstantValueList': [{
            'UR_VALUE': '230', 'IR_VALUE': '2', 'LAST_UPDATED': '06.09.2026 16:59:45'}]}
        self.entry = type('Entry', (), {'entry_id': 'test'})()
        self.coordinator = coordinator_class()(None, self.entry, self.client)
        await self.coordinator._async_setup()

    async def test_save_precedes_submission_and_poll_does_not_resubmit(self):
        async def submit(params):
            self.assertEqual(len(self.coordinator._store.saved['attempts']), 1)
        self.client.async_request_meter_data.side_effect = submit
        first = await self.coordinator._async_update_data()
        second = await self.coordinator._async_update_data()
        self.assertEqual(first, second)
        self.assertEqual(first['pods']['POD']['estimated_power'], .46)
        self.assertEqual(self.client.async_request_meter_data.await_count, 1)
        self.assertEqual(self.client.async_read_meter_data.await_count, 2)

    async def test_storage_failure_prevents_submission(self):
        self.coordinator._store.fail = True
        with self.assertRaises(OSError):
            await self.coordinator._async_update_data()
        self.client.async_request_meter_data.assert_not_awaited()

    async def test_empty_and_failed_reads_keep_coherent_cached_snapshot(self):
        first = await self.coordinator._async_update_data()
        self.client.async_read_meter_data.return_value = {'Result': 'Processing'}
        self.assertEqual(await self.coordinator._async_update_data(), first)
        self.client.async_read_meter_data.side_effect = PortalError('unavailable')
        self.assertEqual(await self.coordinator._async_update_data(), first)

    async def test_restart_retains_quota_and_cached_reading(self):
        first = await self.coordinator._async_update_data()
        new = coordinator_class()(None, self.entry, self.client)
        new._store.saved = deepcopy(self.coordinator._store.saved)
        await new._async_setup()
        self.client.async_read_meter_data.return_value = {'Result': 'Processing'}
        self.assertEqual(await new._async_update_data(), first)
        self.assertEqual(self.client.async_request_meter_data.await_count, 1)

    async def test_failed_submission_is_counted_without_retry(self):
        self.client.async_request_meter_data.side_effect = PortalError('ambiguous failure')
        await self.coordinator._async_update_data()
        await self.coordinator._async_update_data()
        self.assertEqual(self.client.async_request_meter_data.await_count, 1)

    async def test_out_of_order_reading_does_not_replace_newer_values(self):
        first = await self.coordinator._async_update_data()
        self.client.async_read_meter_data.return_value['dataIstantValueList'][0]['LAST_UPDATED'] = '05.09.2026 16:59:45'
        self.assertEqual(await self.coordinator._async_update_data(), first)

    async def test_only_smart_meter_pods_are_polled(self):
        self.client.async_get_pods.return_value = [{'Name': 'POD', 'Smart_meter__c': False}]
        self.assertEqual(await self.coordinator._async_update_data(), {'pods': {}})
        self.client.async_request_meter_data.assert_not_awaited()
        self.client.async_read_meter_data.assert_not_awaited()
