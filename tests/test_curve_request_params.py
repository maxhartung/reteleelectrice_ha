"""Regression tests for the portal's load-curve argument list."""

import ast
import asyncio
from pathlib import Path
import unittest


API_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "reteleelectrice_ro"
    / "api.py"
)


def _api_curve_methods() -> dict[str, object]:
    tree = ast.parse(API_PATH.read_text())
    wanted = {"async_get_load_curve", "async_get_load_curve_csv"}
    methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in wanted
    ]
    namespace = {
        "parse_load_curve_response": lambda value: value,
        "PortalProtocolError": ValueError,
        "LoadCurveMonth": object,
    }
    module = ast.Module(body=methods, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(API_PATH), "exec"), namespace)
    return namespace


class CurveRequestParamsTests(unittest.TestCase):
    def test_monthly_curve_has_portal_placeholder_argument(self) -> None:
        methods = _api_curve_methods()

        class Client:
            async def _call_vf_ws(self, method_name: str, method_params: list[str]) -> object:
                self.called = (method_name, method_params)
                return {"ok": True}

        client = Client()
        asyncio.run(client_async_get_load_curve(methods, client))

        self.assertEqual(client.called[0], "CurveDiCaricoGraph")
        self.assertEqual(
            client.called[1],
            [
                "RO001E110948168",
                "WI",
                "01/08/2026 00:00:00",
                "31/08/2026 23:59:59",
                "",
            ],
        )

    def test_captured_four_argument_curve_is_normalized(self) -> None:
        methods = _api_curve_methods()

        class Client:
            async def _call_vf_ws(self, method_name: str, method_params: list[str]) -> object:
                self.called = (method_name, method_params)
                return {"ok": True}

        client = Client()
        asyncio.run(
            methods["async_get_load_curve_csv"](
                client,
                [
                    "RO001E110948168",
                    "WI",
                    "01/08/2026 00:00:00",
                    "31/08/2026 23:59:59",
                ],
            )
        )

        self.assertEqual(client.called[1][-1], "")


async def client_async_get_load_curve(methods: dict[str, object], client: object) -> object:
    return await methods["async_get_load_curve"](client, "RO001E110948168", 2026, 8)


if __name__ == "__main__":
    unittest.main()
