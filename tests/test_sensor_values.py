"""Tests for smart-meter value extraction."""

import ast
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "reteleelectrice_ro" / "sensor.py"


class SensorValueTests(unittest.TestCase):
    def _helpers(self) -> dict[str, object]:
        tree = ast.parse(MODULE_PATH.read_text())
        wanted = {"_register_value", "_to_number"}
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
        namespace: dict[str, object] = {}
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


if __name__ == "__main__":
    unittest.main()
