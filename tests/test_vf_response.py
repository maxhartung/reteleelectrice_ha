"""Tests for Visualforce A4J response parsing."""

import ast
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "reteleelectrice_ro" / "api.py"


class VfResponseTests(unittest.TestCase):
    def _parser(self):
        tree = ast.parse(MODULE_PATH.read_text())
        wanted = {"_attributes", "_json_or_text", "_json_fragments", "_parse_vf_response"}
        nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                ast.Import(names=[ast.alias(name="html")]),
                ast.Import(names=[ast.alias(name="json")]),
                ast.Import(names=[ast.alias(name="re")]),
                ast.ImportFrom(
                    module="json",
                    names=[ast.alias(name="JSONDecoder")],
                    level=0,
                ),
                *nodes,
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        namespace: dict[str, object] = {}
        exec(compile(module, str(MODULE_PATH), "exec"), namespace)
        return namespace["_parse_vf_response"]

    def test_json_inside_a4j_cdata_is_extracted(self) -> None:
        parse = self._parser()
        payload = (
            "<ajax-response><changes><update id='result'><![CDATA["
            '{"Result":"OK","dataIstantValueList":[{"P_VALUE":"0,123",'
            '"energyReadingList":[{"VALUE":"393,476","ENERGY_TYPE":"EA"}]}]}'
            "]]></update></changes></ajax-response>"
        )
        self.assertEqual(
            parse(payload),
            {
                "Result": "OK",
                "dataIstantValueList": [
                    {
                        "P_VALUE": "0,123",
                        "energyReadingList": [
                            {"VALUE": "393,476", "ENERGY_TYPE": "EA"}
                        ],
                    }
                ],
            },
        )

    def test_plain_csv_text_is_preserved(self) -> None:
        parse = self._parser()
        csv = "Zi;Frecventa;Marime;Q1\n26.08.2026;15;EA;0,123\n"
        self.assertEqual(parse(csv), csv)

    def test_csv_inside_a4j_cdata_is_extracted(self) -> None:
        parse = self._parser()
        csv = "Zi;Frecventa;Marime;Q1\n26.08.2026;15;EA;0,123\n"
        payload = f"<ajax-response><update><![CDATA[{csv}]]></update></ajax-response>"
        self.assertEqual(parse(payload), csv)

    def test_empty_wrapper_object_is_ignored(self) -> None:
        parse = self._parser()
        payload = (
            '<script>var wrapper = {};</script>'
            '<ajax-response><update><![CDATA['
            '{"dataIstantValueList":[{"P_VALUE":"0,123"}]}'
            ']]></update></ajax-response>'
        )
        self.assertEqual(
            parse(payload),
            {"dataIstantValueList": [{"P_VALUE": "0,123"}]},
        )


if __name__ == "__main__":
    unittest.main()
