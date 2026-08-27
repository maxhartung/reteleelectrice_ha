"""Tests for Salesforce Visualforce login field discovery."""

import ast
import html
from pathlib import Path
import re
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


if __name__ == "__main__":
    unittest.main()
