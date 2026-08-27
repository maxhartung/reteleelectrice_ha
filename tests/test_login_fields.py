"""Tests for Salesforce Visualforce login field discovery."""

import ast
import html
import json
from pathlib import Path
import re
from urllib.parse import quote
import unittest


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "reteleelectrice_ro" / "api.py"


class LoginFieldTests(unittest.TestCase):
    def _helpers(self) -> dict[str, object]:
        tree = ast.parse(MODULE_PATH.read_text())
        wanted = {"_attributes", "_find_field_name", "_submit_field"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        nodes.insert(
            0,
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
        )
        ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        namespace: dict[str, object] = {"html": html, "re": re}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MODULE_PATH), "exec"), namespace)
        return namespace

    def test_obfuscated_password_field_uses_password_input_type(self) -> None:
        namespace = self._helpers()
        field_name = namespace["_find_field_name"](
            '<form><input type="text" name="j_id_user"><input type="password" name="j_id_pw"></form>',
            ("password", "pw"),
        )
        self.assertEqual(field_name, "j_id_pw")

    def test_submit_field_is_included(self) -> None:
        namespace = self._helpers()
        submit = namespace["_submit_field"](
            '<form><input type="submit" name="loginButton" value="Login"></form>'
        )
        self.assertEqual(submit, ("loginButton", "Login"))

    def test_visualforce_javascript_submit_field_is_detected(self) -> None:
        namespace = self._helpers()
        submit = namespace["_submit_field"](
            "function logincall() { jsfcljs(document.forms['loginPage:loginForm'],"
            "'loginPage:loginForm:j_id25,loginPage:loginForm:j_id25',''); }"
        )
        self.assertEqual(
            submit,
            ("loginPage:loginForm:j_id25", "loginPage:loginForm:j_id25"),
        )

    def test_runtime_config_is_decoded_from_script_url(self) -> None:
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)
        nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_runtime_configs"
        ]
        module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                ast.Import(names=[ast.alias(name="json")]),
                ast.Import(names=[ast.alias(name="re")]),
                ast.ImportFrom(
                    module="urllib.parse",
                    names=[ast.alias(name="unquote")],
                    level=0,
                ),
                *nodes,
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        namespace: dict[str, object] = {}
        exec(compile(module, str(MODULE_PATH), "exec"), namespace)
        config = {"fwuid": "current-fwuid", "loaded": {"APPLICATION@markup://siteforce:communityApp": "current-app"}}
        html_value = "/s/sfsites/l/" + quote(
            json.dumps(config, separators=(",", ":")), safe=""
        ) + "/resources.js"
        self.assertEqual(namespace["_runtime_configs"](html_value), [config])


if __name__ == "__main__":
    unittest.main()
