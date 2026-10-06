"""Exercise the actual coordinator against fake network and storage boundaries."""
from __future__ import annotations
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
from types import SimpleNamespace
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


class ClientError(Exception):
    pass


class SessionExpiredError(AuthError):
    pass


class Store:
    def __init__(self, *args, **kwargs):
        self.saved = None
        self.events = []
        self.fail = False

    async def async_save(self, data):
        self.events.append('save')
        try:
            await self._async_write_data(data)
        except OSError:
            pass  # Home Assistant Store logs WriteError instead of raising it.

    async def _async_write_data(self, data):
        if self.fail:
            raise OSError('disk unavailable')
        self.saved = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.saved)


def confirmed_store_class():
    tree = ast.parse((ROOT / 'storage.py').read_text())
    tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    namespace = {'Store': Store}
    exec(compile(tree, 'storage.py', 'exec'), namespace)
    return namespace['ConfirmedStore']


def coordinator_class():
    tree = ast.parse((ROOT / 'coordinator.py').read_text())
    # Stub only HA's framework boundary; execute the real coordinator unchanged.
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom)) or
                 isinstance(n, ast.ImportFrom) and n.module == '__future__']
    namespace = dict(Any=object, DataUpdateCoordinator=CoordinatorBase, ConfirmedStore=confirmed_store_class(), datetime=datetime,
                     timezone=timezone, logging=logging, DOMAIN='reteleelectrice_ro',
                     DEFAULT_UPDATE_INTERVAL=timedelta(minutes=5), PortalError=PortalError,
                     AuthenticationError=AuthError, ConfigEntryAuthFailed=AuthError,
                     SessionExpiredError=SessionExpiredError,
                     UpdateFailed=PortalError, ConsumptionRequestState=load('consumption_request').ConsumptionRequestState,
                     aiohttp=SimpleNamespace(ClientError=ClientError),
                     parse_meter=load('meter').parse_meter)
    exec(compile(tree, 'coordinator.py', 'exec'), namespace)
    return namespace['ReteleElectriceCoordinator']


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncMock()
        self.client.is_logged_in = True
        self.client.async_get_pods.return_value = [{'Name': 'POD', 'Smart_meter__c': True}]
        self.client.async_meter_params.return_value = ['', '', 'POD']
        self.client.async_read_meter_data.return_value = {'dataIstantValueList': [{
            'UR_VALUE': '230', 'IR_VALUE': '2', 'LAST_UPDATED': '06.09.2026 16:59:45',
            'energyReadingList': [{'ENERGY_TYPE': 'EA', 'VALUE': '496,220'}]}]}
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

    async def test_expired_submission_renews_session_without_resubmitting(self):
        self.client.async_request_meter_data.side_effect = AuthError('expired')
        result = await self.coordinator._async_update_data()
        self.client.async_relogin.assert_awaited_once()
        self.client.async_request_meter_data.assert_awaited_once()
        self.assertEqual(result['pods']['POD']['energy_import'], 496.22)
        await self.coordinator._async_update_data()
        self.client.async_request_meter_data.assert_awaited_once()

    async def test_invalid_credentials_after_renewal_require_reauth(self):
        self.client.async_request_meter_data.side_effect = AuthError('expired')
        self.client.async_relogin.side_effect = AuthError('invalid credentials')
        with self.assertRaises(AuthError):
            await self.coordinator._async_update_data()

    async def test_partial_newer_snapshot_preserves_complete_previous_reading(self):
        first = await self.coordinator._async_update_data()
        self.client.async_read_meter_data.return_value = {'dataIstantValueList': [
            {'LAST_UPDATED': '06.09.2026 17:00:00'}]}
        self.assertEqual(await self.coordinator._async_update_data(), first)
        self.assertEqual(self.coordinator._store.saved['meters'], first['pods'])

    async def test_empty_initial_discovery_retries_setup(self):
        self.client.async_get_pods.return_value = []
        with self.assertRaises(PortalError):
            await self.coordinator._async_update_data()
        self.client.async_request_meter_data.assert_not_awaited()

    async def test_malformed_discovery_retries_setup(self):
        self.client.async_get_pods.return_value = {'Result': 'unavailable'}
        with self.assertRaises(PortalError):
            await self.coordinator._async_update_data()

    async def test_realistic_swallowed_write_failure_preserves_previous_disk_state(self):
        await self.coordinator._async_update_data()
        before = deepcopy(self.coordinator._store.saved)
        self.coordinator._store.fail = True
        with self.assertRaises(OSError):
            await self.coordinator._save()
        self.assertEqual(self.coordinator._store.saved, before)

    async def test_timed_out_submission_is_counted_and_existing_result_is_read(self):
        self.client.async_request_meter_data.side_effect = TimeoutError()
        result = await self.coordinator._async_update_data()
        self.assertEqual(result['pods']['POD']['energy_import'], 496.22)
        await self.coordinator._async_update_data()
        self.client.async_request_meter_data.assert_awaited_once()
        self.assertEqual(len(self.coordinator._store.saved['attempts']), 1)

    async def test_network_failure_on_meter_read_keeps_cached_snapshot(self):
        first = await self.coordinator._async_update_data()
        self.client.async_read_meter_data.side_effect = ClientError('connection lost')
        self.assertEqual(await self.coordinator._async_update_data(), first)
        self.assertEqual(self.coordinator.diagnostics['meter_statuses'], ['ClientError'])

    async def test_diagnostics_distinguish_connection_auth_and_missing_data(self):
        self.client.async_get_pods.side_effect = TimeoutError()
        with self.assertRaises(PortalError):
            await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.diagnostics['poll_status'], 'connection_failed')
        self.client.async_get_pods.side_effect = AuthError()
        with self.assertRaises(AuthError):
            await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.diagnostics['poll_status'], 'authentication_failed')
        self.client.async_get_pods.side_effect = None
        self.client.async_read_meter_data.return_value = {'Result': 'Processing'}
        await self.coordinator._async_update_data()
        diagnostics = self.coordinator.diagnostics
        self.assertEqual(diagnostics['poll_status'], 'ok')
        self.assertEqual(diagnostics['meter_statuses'], ['no_complete_reading'])
        self.assertNotIn('POD', repr(diagnostics))

    async def test_rejected_renewed_session_retries_instead_of_requiring_password(self):
        self.client.async_get_pods.side_effect = SessionExpiredError()
        with self.assertRaises(PortalError) as ctx:
            await self.coordinator._async_update_data()
        self.assertNotIsInstance(ctx.exception, AuthError)
        self.assertEqual(self.coordinator.diagnostics['poll_status'], 'session_expired')

    async def test_empty_account_response_after_working_poll_renews_session(self):
        first = await self.coordinator._async_update_data()
        self.client.async_get_pods.side_effect = [[], [{'Name': 'POD', 'Smart_meter__c': True}]]
        self.assertEqual(await self.coordinator._async_update_data(), first)
        self.client.async_relogin.assert_awaited_once()
        self.client.async_request_meter_data.assert_awaited_once()
        self.assertEqual(self.client.async_read_meter_data.await_count, 2)

    async def test_persistently_empty_account_retries_only_once_per_poll(self):
        self.client.async_get_pods.return_value = []
        with self.assertRaises(PortalError):
            await self.coordinator._async_update_data()
        self.assertEqual(self.client.async_get_pods.await_count, 2)
        self.client.async_relogin.assert_awaited_once()
        self.client.async_request_meter_data.assert_not_awaited()

    async def test_empty_account_recovery_does_not_turn_temporary_login_failure_into_reauth(self):
        self.client.async_get_pods.return_value = []
        self.client.async_relogin.side_effect = PortalError('login server unavailable')
        with self.assertRaises(PortalError) as ctx:
            await self.coordinator._async_update_data()
        self.assertNotIsInstance(ctx.exception, AuthError)
        self.client.async_relogin.assert_awaited_once()
        self.client.async_request_meter_data.assert_not_awaited()
