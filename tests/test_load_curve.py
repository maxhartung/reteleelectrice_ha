"""Tests for the pure load-curve parser."""

from pathlib import Path
import importlib.util
import sys
import unittest

PARSER_PATH = Path(__file__).parents[1] / "custom_components" / "reteleelectrice_ro" / "load_curve.py"
PARSER_SPEC = importlib.util.spec_from_file_location("reteleelectrice_load_curve", PARSER_PATH)
assert PARSER_SPEC and PARSER_SPEC.loader
PARSER_MODULE = importlib.util.module_from_spec(PARSER_SPEC)
sys.modules[PARSER_SPEC.name] = PARSER_MODULE
PARSER_SPEC.loader.exec_module(PARSER_MODULE)
LoadCurveParseError = PARSER_MODULE.LoadCurveParseError
parse_load_curve_csv = PARSER_MODULE.parse_load_curve_csv


FIXTURE = Path(__file__).parent / "fixtures" / "load_curve_sample.csv"


class LoadCurveTests(unittest.TestCase):
    def test_parse_registers_and_totals(self) -> None:
        curve = parse_load_curve_csv(FIXTURE.read_bytes())

        self.assertEqual(curve.day.isoformat(), "2026-08-26")
        self.assertEqual(curve.frequency_minutes, 15)
        self.assertEqual(curve.interval_count, 4)
        self.assertEqual(curve.registers["EA"], (0.091, 0.131, 0.139, 0.136))
        self.assertAlmostEqual(curve.total("EA"), 0.497)
        self.assertEqual(curve.samples("EA")[1][0].hour, 0)
        self.assertEqual(curve.samples("EA")[1][0].minute, 15)

    def test_missing_register_is_zero(self) -> None:
        curve = parse_load_curve_csv(FIXTURE.read_text())
        self.assertEqual(curve.total("UNKNOWN"), 0)

    def test_dot_decimal_is_preserved(self) -> None:
        curve = parse_load_curve_csv(
            "Zi;Frecventa;Marime;Q1\n2026.01.01;15;EA;1.25\n"
        )
        self.assertEqual(curve.registers["EA"], (1.25,))

    def test_rejects_invalid_header(self) -> None:
        with self.assertRaises(LoadCurveParseError):
            parse_load_curve_csv("a;b;c;Q1\n2026.01.01;15;EA;0,1\n")


if __name__ == "__main__":
    unittest.main()
