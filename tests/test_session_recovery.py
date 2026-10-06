"""Exercise the real portal client using fake HTTP responses, without HA installed."""
from __future__ import annotations

import ast
import html
import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import unquote, urljoin, urlsplit
import unittest
from unittest.mock import AsyncMock
import uuid

ROOT = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro'


def api_namespace():
    tree = ast.parse((ROOT / 'api.py').read_text())
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 or isinstance(node, ast.ClassDef) and node.name != 'AuraBootstrap']
    ns = dict(html=html, json=json, logging=logging, re=re, uuid=uuid,
              unquote=unquote, urljoin=urljoin, urlsplit=urlsplit,
              LOGGER=logging.getLogger(__name__), REQUEST_TIMEOUT=None, VF_TIMEOUT=None,
              BASE_URL='https://example.test', LOGIN_PAGE='https://example.test/PEDRO_SiteLogin',
              AURA_URL='https://example.test/s/sfsites/aura',
              VF_PAGE_MAP={'FindOutMeterInstantData': 'meter', 'ReqMeterInstantData': 'meter'})
    exec(compile(tree, str(ROOT / 'api.py'), 'exec'), ns)
    return ns


class Response:
    def __init__(self, payload='', status=200, url='https://example.test/meter'):
        self.payload = json.dumps(payload) if isinstance(payload, (dict, list)) else payload
        self.status = status
        self.url = url
        self.headers = {}

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def text(self): return self.payload


class Session:
    closed = False

    def __init__(self, *, gets=(), posts=()):
        self.gets = iter(gets)
        self.posts = iter(posts)
        self.post_count = 0

    def get(self, *args, **kwargs): return next(self.gets)
    def post(self, *args, **kwargs):
        self.post_count += 1
        return next(self.posts)


VF_FORM = '<form id="meter"><input name="com.salesforce.visualforce.ViewState" value="state"></form>'
READING = {'Result': 'OK', 'dataIstantValueList': []}


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = api_namespace()

    def client(self, session):
        client = self.ns['ReteleElectriceClient']('email', 'password', session)

        async def login():
            client._logged_in = True
            client._bootstrap = SimpleNamespace(fwuid='fw', app_uid='app', token='token')

        client.async_login = AsyncMock(side_effect=login)
        client._logged_in = True
        client._bootstrap = SimpleNamespace(fwuid='fw', app_uid='app', token='token')
        return client

    async def test_aura_invalid_session_exception_renews_once(self):
        session = Session(posts=[
            Response({'exceptionEvent': True, 'event': {'descriptor': 'aura:invalidSession'}}),
            Response({'actions': [{'state': 'SUCCESS', 'returnValue': [{'Name': 'POD'}]}]}),
        ])
        client = self.client(session)
        self.assertEqual(await client.async_get_pods(), [{'Name': 'POD'}])
        client.async_login.assert_awaited_once()

    async def test_aura_invalid_session_id_renews_once(self):
        session = Session(posts=[
            Response({'actions': [{'state': 'ERROR', 'error': [{'message': 'INVALID_SESSION_ID'}]}]}),
            Response({'actions': [{'state': 'SUCCESS', 'returnValue': []}]}),
        ])
        client = self.client(session)
        self.assertEqual(await client.async_get_pods(), [])
        client.async_login.assert_awaited_once()

    async def test_unrecognized_aura_response_is_failure_after_one_renewal(self):
        error = {'exceptionEvent': True, 'event': {'descriptor': 'aura:unknown'}}
        client = self.client(Session(posts=[Response(error), Response(error)]))
        with self.assertRaises(self.ns['PortalProtocolError']):
            await client.async_get_pods()
        client.async_login.assert_awaited_once()

    async def test_stale_aura_metadata_is_refreshed_without_a_restart(self):
        session = Session(posts=[
            Response('<html>Unexpected framework response</html>'),
            Response({'actions': [{'state': 'SUCCESS', 'returnValue': [{'Name': 'POD'}]}]}),
        ])
        client = self.client(session)
        self.assertEqual(await client.async_get_pods(), [{'Name': 'POD'}])
        client.async_login.assert_awaited_once()

    async def test_saved_credentials_rejected_during_renewal_are_not_retried(self):
        client = self.client(Session(posts=[Response(status=401)]))
        client.async_login.side_effect = self.ns['AuthenticationError']('credentials rejected')
        with self.assertRaises(self.ns['AuthenticationError']):
            await client.async_get_pods()
        client.async_login.assert_awaited_once()

    async def test_recreated_http_session_requires_a_new_login(self):
        client = self.client(Session())
        client._session.closed = True
        await client._ensure_login()
        client.async_login.assert_awaited_once()

    async def test_vf_page_401_and_403_trigger_read_recovery(self):
        for status in (401, 403):
            session = Session(gets=[Response(status=status), Response(VF_FORM)], posts=[Response(READING)])
            client = self.client(session)
            self.assertEqual(await client.async_read_meter_data(['', '', 'POD']), READING)
            client.async_login.assert_awaited_once()
            self.assertEqual(session.post_count, 1)

    async def test_vf_redirect_to_community_login_triggers_recovery(self):
        client = self.client(Session(
            gets=[Response('<html>Login</html>', url='https://example.test/s/login'), Response(VF_FORM)],
            posts=[Response(READING)]))
        self.assertEqual(await client.async_read_meter_data(['', '', 'POD']), READING)
        client.async_login.assert_awaited_once()

    async def test_vf_structured_session_error_triggers_recovery(self):
        client = self.client(Session(gets=[Response(VF_FORM), Response(VF_FORM)], posts=[
            Response({'Result': 'ERROR', 'ErrorMessage': 'INVALID_SESSION_ID'}), Response(READING)]))
        self.assertEqual(await client.async_read_meter_data(['', '', 'POD']), READING)
        client.async_login.assert_awaited_once()

    async def test_read_stops_after_one_unsuccessful_renewal(self):
        client = self.client(Session(gets=[Response(status=403), Response(status=403)]))
        with self.assertRaises(self.ns['AuthenticationError']):
            await client.async_read_meter_data(['', '', 'POD'])
        client.async_login.assert_awaited_once()

    async def test_submission_is_not_replayed_on_structured_session_error(self):
        session = Session(gets=[Response(VF_FORM)], posts=[
            Response({'Result': 'ERROR', 'ErrorMessage': 'INVALID_SESSION_ID'})])
        client = self.client(session)
        with self.assertRaises(self.ns['AuthenticationError']):
            await client.async_request_meter_data(['', '', 'POD'])
        self.assertEqual(session.post_count, 1)
        client.async_login.assert_not_awaited()

    async def test_failed_login_discards_previous_bootstrap(self):
        client = self.ns['ReteleElectriceClient']('email', 'password', Session(gets=[Response(status=503)]))
        client._logged_in = True
        client._bootstrap = object()
        with self.assertRaises(self.ns['PortalError']):
            await client.async_login()
        self.assertFalse(client.is_logged_in)
        self.assertIsNone(client._bootstrap)

    def test_login_detection_ignores_start_url_query(self):
        detect = self.ns['_looks_like_login_page']
        self.assertTrue(detect('https://example.test/s/login?startURL=/meter'))
        self.assertFalse(detect('https://example.test/meter?startURL=/PEDRO_SiteLogin'))
        self.assertTrue(detect('https://example.test/', "<input type='password' name='pw'>"))
