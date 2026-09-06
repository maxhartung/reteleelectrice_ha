"""Exercise request acknowledgement and transient login failure handling."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

PATH = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro/api.py'


def client_class():
    tree = ast.parse(PATH.read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in {'PortalError', 'AuthenticationError', 'PortalProtocolError'}:
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == '_looks_like_auth_error':
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == 'ReteleElectriceClient':
            node.body = [method for method in node.body if isinstance(method, ast.AsyncFunctionDef) and method.name in
                         {'async_request_meter_data', 'async_relogin', 'async_login'}]
            selected.append(node)
    tree.body = selected
    namespace = dict(LOGIN_PAGE='https://example.test/login', BASE_URL='https://example.test', REQUEST_TIMEOUT=None)
    exec(compile(tree, str(PATH), 'exec'), namespace)
    return namespace


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = client_class()
        self.client = self.ns['ReteleElectriceClient']()
        self.client._call_vf_ws_once = AsyncMock()

    async def test_rejected_acknowledgement_raises_without_retry(self):
        self.client._call_vf_ws_once.return_value = {'Result':'ERROR', 'ErrorMessage':'Quota exceeded'}
        with self.assertRaises(self.ns['PortalError']):
            await self.client.async_request_meter_data(['', '', 'POD'])
        self.client._call_vf_ws_once.assert_awaited_once()

    async def test_unknown_acknowledgement_is_not_success(self):
        for result in ({}, 'unexpected', {'Result':'pending'}):
            self.client._call_vf_ws_once.return_value = result
            with self.assertRaises(self.ns['PortalError']):
                await self.client.async_request_meter_data(['', '', 'POD'])

    async def test_ok_acknowledgement_accepted(self):
        result = {'Result':'OK', 'ErrorMessage':None}
        self.client._call_vf_ws_once.return_value = result
        self.assertEqual(await self.client.async_request_meter_data(['', '', 'POD']), result)

    async def test_auth_payload_signals_session_renewal(self):
        self.client._call_vf_ws_once.return_value = {'Result':'ERROR', 'ErrorMessage':'Session expired'}
        with self.assertRaises(self.ns['AuthenticationError']):
            await self.client.async_request_meter_data(['', '', 'POD'])

    async def test_relogin_invalidates_stale_bootstrap(self):
        self.client._logged_in = True
        self.client._bootstrap = object()
        self.client.async_login = AsyncMock()
        await self.client.async_relogin()
        self.assertFalse(self.client._logged_in)
        self.assertIsNone(self.client._bootstrap)
        self.client.async_login.assert_awaited_once()

    async def test_login_http_503_is_retryable_not_invalid_credentials(self):
        class Response:
            status = 503
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Session:
            def get(self, *args, **kwargs): return Response()
        self.client._get_session = AsyncMock(return_value=Session())
        with self.assertRaises(self.ns['PortalError']) as ctx:
            await self.client.async_login()
        self.assertNotIsInstance(ctx.exception, self.ns['AuthenticationError'])
