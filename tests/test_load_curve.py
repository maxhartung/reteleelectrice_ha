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
parse_load_curve_csv_month = PARSER_MODULE.parse_load_curve_csv_month
parse_load_curve_response = PARSER_MODULE.parse_load_curve_response


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

    def test_interval_values_are_aggregated_to_hours(self) -> None:
        curve = parse_load_curve_csv(FIXTURE.read_text())
        self.assertEqual(curve.hourly_totals("EA")[0], 0.497)
        self.assertIsNone(curve.hourly_totals("EA")[1])

    def test_monthly_csv_keeps_multiple_days(self) -> None:
        payload = (
            "Zi;Frecventa;Marime;Q1;Q2\n"
            "24.08.2026;60;EA;0,5;0,6\n"
            "25.08.2026;60;EA;0,7;0,8\n"
        )
        month = parse_load_curve_csv_month(payload)
        self.assertEqual([item.day.isoformat() for item in month.days], ["2026-08-24", "2026-08-25"])
        self.assertEqual(month.daily_totals(), {"2026-08-24": 1.1, "2026-08-25": 1.5})

    def test_structured_monthly_response_is_supported(self) -> None:
        month = parse_load_curve_response(
            {
                "Result": "OK",
                "data": [
                    {"date": "24.08.2026", "frequency": 60, "register": "EA", "values": ["0,5", "0,6"]},
                    {"date": "25.08.2026", "frequency": 60, "register": "WI", "values": ["0,7", "0,8"]},
                ],
            }
        )
        self.assertEqual(month.daily_totals(), {"2026-08-24": 1.1, "2026-08-25": 1.5})
        self.assertEqual(month.latest_day.day.isoformat(), "2026-08-25")

    def test_portal_sample_values_response_is_supported(self) -> None:
        month = parse_load_curve_response(
            [
                {
                    "sampleValues": "0,5;0,6;0,7",
                    "sampleDate": "26/08/2026 00:00",
                    "energyType": "WI",
                }
            ]
        )
        self.assertEqual(month.latest_day.frequency_minutes, 60)
        self.assertEqual(month.latest_day.hourly_totals(), (0.5, 0.6, 0.7, *([None] * 21)))

    def test_portal_hourly_csv_is_supported(self) -> None:
        csv = (
            "Zi;00:00 - 01:00;01:00 - 02:00;23:00 - 00:00\n"
            '"26.08.2026";"0,5";"0,6";"1,4"\n'
        )
        month = parse_load_curve_response(csv)
        self.assertEqual(month.latest_day.frequency_minutes, 60)
        self.assertEqual(month.latest_day.registers["EA"], (0.5, 0.6, *([None] * 21), 1.4))

    def test_hourly_sample_objects_are_merged(self) -> None:
        month = parse_load_curve_response(
            [
                {"sampleDate": "24.08.2026", "sampleHour": 0, "sampleValue": "0,5"},
                {"sampleDate": "24.08.2026", "sampleHour": 1, "sampleValue": "0,6"},
            ]
        )
        self.assertEqual(month.latest_day.hourly_totals(), (0.5, 0.6, *([None] * 22)))

    def test_date_keyed_values_are_supported(self) -> None:
        month = parse_load_curve_response({"data": {"24.08.2026": ["0,5", "0,6"]}})
        self.assertEqual(month.daily_totals(), {"2026-08-24": 1.1})

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
