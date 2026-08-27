"""Tests for active-power derivation from cumulative meter readings."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "reteleelectrice_ro"
    / "average_power.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("reteleelectrice_average_power", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = MODULE
MODULE_SPEC.loader.exec_module(MODULE)
calculate_average_active_power = MODULE.calculate_average_active_power
instant_active_energy = MODULE.instant_active_energy
instant_timestamp = MODULE.instant_timestamp


class AveragePowerTests(unittest.TestCase):
    def test_active_register_and_meter_timestamp_are_extracted(self) -> None:
        instant = {
            "dataIstantValueList": [
                {
                    "energyReadingList": [
                        {"ENERGY_TYPE": "EA", "VALUE": "395,530"},
                    ],
                    "LAST_UPDATED": "28.08.2026 00:15:20",
                }
            ]
        }
        fallback = datetime(2026, 8, 28, 1, tzinfo=timezone.utc)

        self.assertEqual(instant_active_energy(instant), 395.53)
        self.assertEqual(
            instant_timestamp(instant, fallback),
            datetime(2026, 8, 28, 0, 15, 20, tzinfo=timezone.utc),
        )

    def test_average_power_is_delta_energy_over_time(self) -> None:
        previous_time = datetime(2026, 8, 28, 0, tzinfo=timezone.utc)
        current_time = previous_time + timedelta(hours=2)
        self.assertEqual(
            calculate_average_active_power(
                (395.0, previous_time),
                (396.0, current_time),
            ),
            0.5,
        )

    def test_first_reading_and_meter_reset_are_unknown(self) -> None:
        current = (395.53, datetime(2026, 8, 28, tzinfo=timezone.utc))
        self.assertIsNone(calculate_average_active_power(None, current))
        self.assertIsNone(
            calculate_average_active_power(
                (396.0, datetime(2026, 8, 27, tzinfo=timezone.utc)),
                current,
            )
        )


if __name__ == "__main__":
    unittest.main()
