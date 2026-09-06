"""Verify portal units and timestamps, without Home Assistant installed."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro/meter.py'
spec = importlib.util.spec_from_file_location('meter', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MeterTests(unittest.TestCase):
    def reading(self, **values):
        values.setdefault('LAST_UPDATED', '06.09.2026 16:59:45')
        values.setdefault('energyReadingList', [{'ENERGY_TYPE': 'EA', 'VALUE': '496,220'}])
        return module.parse_meter({'dataIstantValueList': [values]})

    def test_screenshot_values_estimate_power(self):
        result = self.reading(UR_VALUE='229,430', IR_VALUE='12,283', LAST_UPDATED='06.09.2026 16:59:45',
                              energyReadingList=[{'ENERGY_TYPE': 'EA', 'VALUE': '496,220'}])
        self.assertEqual(result, {'voltage': 229.43, 'current': 12.283, 'estimated_power': 2.818, 'energy_import': 496.22,
                                  'last_update': '2026-09-06T16:59:45+03:00'})

    def test_timestamp_uses_romanian_winter_timezone(self):
        self.assertEqual(module.website_timestamp('06.01.2026 16:59:45').utcoffset().total_seconds(), 7200)

    def test_zero_current_is_valid(self):
        self.assertEqual(self.reading(UR_VALUE='230', IR_VALUE='0')['estimated_power'], 0)

    def test_missing_current_does_not_manufacture_power(self):
        self.assertIsNone(self.reading(UR_VALUE='230'))

    def test_missing_update_time_never_uses_reading_date_or_poll_time(self):
        self.assertIsNone(self.reading(UR_VALUE='230', IR_VALUE='2', LAST_UPDATED=None, READING_DATE='06.09.2026'))

    def test_empty_response_retains_cache(self):
        self.assertIsNone(module.parse_meter({'Result': 'Processing'}))
        self.assertIsNone(module.parse_meter({'dataIstantValueList': []}))

    def test_invalid_numbers_are_unavailable(self):
        for value in ('NaN', 'inf', '-1', True, '', None):
            self.assertIsNone(module.number(value))
        self.assertEqual(module.number('1.234,56'), 1234.56)

    def test_nested_case_insensitive_payload(self):
        result = module.parse_meter({'result': {'dataistantvaluelist': [{'ur_value': '230','ir_value': '2', 'last_updated':'06.09.2026 16:59:45', 'energyreadinglist':[{'energy_type':'EA','value':'496,220'}]}]}})
        self.assertEqual(result['estimated_power'], .46)

    def test_missing_energy_rejects_snapshot(self):
        self.assertIsNone(self.reading(UR_VALUE='230', IR_VALUE='2', energyReadingList=[]))

    def test_zero_energy_is_valid(self):
        self.assertEqual(self.reading(UR_VALUE='230', IR_VALUE='2',
            energyReadingList=[{'ENERGY_TYPE':'EA','VALUE':'0,000'}])['energy_import'], 0)
