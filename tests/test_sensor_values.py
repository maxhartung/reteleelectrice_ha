"""Tests for smart-meter value extraction."""

import ast
from datetime import datetime
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "reteleelectrice_ro" / "sensor.py"


class SensorValueTests(unittest.TestCase):
    def _helpers(self) -> dict[str, object]:
        tree = ast.parse(MODULE_PATH.read_text())
        wanted = {
            "_walk_values",
            "_register_value",
            "_to_number",
            "_rows",
            "_get_energy_value",
            "_data_map",
            "_reading_datetime",
            "_archive_readings",
            "_archive_years",
        }
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
                *nodes,
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        namespace: dict[str, object] = {"datetime": datetime}
        exec(compile(module, str(MODULE_PATH), "exec"), namespace)
        return namespace

    def test_typed_energy_register_is_found(self) -> None:
        namespace = self._helpers()
        result = namespace["_register_value"](
            {"energyReadingList": [{"ENERGY_TYPE": "EA", "VALUE": "393,476"}]},
            "EA",
        )
        self.assertEqual(result, "393,476")

    def test_romanian_decimal_is_converted(self) -> None:
        namespace = self._helpers()
        self.assertEqual(namespace["_to_number"]("393,476"), 393.476)
        self.assertEqual(namespace["_to_number"]("1.234,56"), 1234.56)

    def test_instant_response_register_is_found(self) -> None:
        namespace = self._helpers()
        instant = {
            "dataIstantValueList": [
                {
                    "energyReadingList": [
                        {"ENERGY_TYPE": "EA", "VALUE": "393,476"},
                        {"ENERGY_TYPE": "ER", "VALUE": "1,25"},
                    ]
                }
            ]
        }
        self.assertEqual(namespace["_get_energy_value"](instant, "EA"), 393.476)
        self.assertEqual(namespace["_get_energy_value"](instant, "ER"), 1.25)

    def test_nested_fields_are_case_insensitive(self) -> None:
        namespace = self._helpers()
        self.assertEqual(
            namespace["_walk_values"](
                {"data": {"sum_ea": "12,5", "p_value": "0,42"}},
                ("SUM_EA",),
            ),
            "12,5",
        )

    def test_archive_years_are_limited_to_two_latest(self) -> None:
        namespace = self._helpers()
        coordinator = type(
            "Coordinator",
            (),
            {
                "data": {
                    "reading_archive": {
                        "POD": {
                            "XML_Readings": [
                                {"measureDate": "01.01.2024", "meter": []},
                                {"measureDate": "01.01.2025", "meter": []},
                                {"measureDate": "01.01.2026", "meter": []},
                            ]
                        }
                    }
                }
            },
        )()
        self.assertEqual(namespace["_archive_years"](coordinator, "POD"), [2026, 2025])


if __name__ == "__main__":
    unittest.main()
