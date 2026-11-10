"""Detailed intervals must not silently become inaccurate energy statistics."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro/load_curve.py'
spec = importlib.util.spec_from_file_location('load_curve', PATH)
curve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curve)


def payload(day='2026-11-09', values=None, frequency='15'):
    return {'result': 'OK', 'ListData': [{'DateT': day, 'ReadingType': 'WI',
            'Frequency': frequency, 'Value': values if values is not None else ['0,125'] * 96}]}


class CurveTests(unittest.TestCase):
    def test_quarters_are_energy_not_power_and_first_hour_has_baseline(self):
        days = curve.parse_load_data(payload())
        stats = curve.hourly_energy(days, 'WI')
        self.assertEqual(len(stats), 25)
        self.assertEqual(stats[0]['sum'], 0)
        self.assertEqual(stats[1]['sum'], .5)
        self.assertEqual(stats[-1]['sum'], 12)
        self.assertEqual(stats[1]['start'].isoformat(), '2026-11-08T22:00:00+00:00')
        self.assertEqual(curve.hourly_energy(days, 'WE'), [])

    def test_missing_interval_does_not_become_zero_or_partial_hour(self):
        values = ['0,125'] * 96
        values[2] = ''
        stats = curve.hourly_energy(curve.parse_load_data(payload(values=values)), 'WI')
        self.assertEqual(len(stats), 24)
        self.assertEqual(stats[-1]['sum'], 11.5)

    def test_fixed_slots_on_dst_day_are_retained_without_guessed_timestamps(self):
        days = curve.parse_load_data(payload(day='2026-10-25'))
        self.assertEqual(len(days['2026-10-25']['WI']['values']), 96)
        self.assertEqual(curve.hourly_energy(days, 'WI'), [])
        stats = curve.hourly_energy(curve.parse_load_data(payload(day='2026-10-25', values=['0,125'] * 100)), 'WI')
        self.assertEqual(len(stats), 26)
        self.assertEqual(stats[-1]['sum'], 12.5)
        self.assertEqual(len({s['start'] for s in stats}), 26)

    def test_idempotent_replay_and_corrections_rebuild_running_sum(self):
        days = curve.parse_load_data(payload())
        self.assertEqual(curve.hourly_energy(days, 'WI'), curve.hourly_energy(days, 'WI'))
        days['2026-11-09']['WI']['values'][0] = .25
        self.assertEqual(curve.hourly_energy(days, 'WI')[-1]['sum'], 12.125)

    def test_reject_nonfinite_negative_and_bad_responses(self):
        for value in ('NaN', 'inf', '-1'):
            with self.assertRaises(ValueError):
                curve.parse_load_data(payload(values=[value] * 96))
        with self.assertRaises(ValueError):
            curve.parse_load_data({'result': 'ERROR'})


    def test_delayed_earlier_history_overwrites_old_baseline_even_in_gap(self):
        # Model recorder's timestamp upsert: omitted timestamps stay in DB.
        days = curve.parse_load_data(payload())
        database = {s['start']: s['sum'] for s in curve.hourly_energy(days, 'WI')}
        old_baseline = min(database)
        values = ['0,125'] * 92 + [''] * 4
        days.update(curve.parse_load_data(payload(day='2026-11-08', values=values)))
        database.update({s['start']: s['sum'] for s in curve.hourly_energy(days, 'WI')})
        self.assertEqual(database[old_baseline], 11.5)
        first_hour = old_baseline + curve.timedelta(hours=1)
        self.assertEqual(database[first_hour] - database[old_baseline], .5)
        totals = [value for _, value in sorted(database.items())]
        self.assertEqual(totals, sorted(totals))
        # Correcting the missing hour replaces the carry-forward total too.
        days.update(curve.parse_load_data(payload(day='2026-11-08')))
        database.update({s['start']: s['sum'] for s in curve.hourly_energy(days, 'WI')})
        self.assertEqual(database[old_baseline], 12)
        self.assertEqual(database[first_hour] - database[old_baseline], .5)

    def test_row_merge_preserves_readings_for_incompatible_partial_shapes(self):
        previous = {'frequency': 15, 'values': [.125] * 96}
        for frequency, count in ((15, 92), (30, 47), (60, 23)):
            fresh = {'frequency': frequency, 'values': [.25] * count}
            self.assertEqual(curve.merge_load_row('2026-11-09', previous, fresh), previous)
        fresh = {'frequency': 30, 'values': [.25] * 48}
        self.assertEqual(curve.merge_load_row('2026-11-09', previous, fresh), fresh)
        missing = {'frequency': 15, 'values': [None] + [.25] * 95}
        merged = curve.merge_load_row('2026-11-09', previous, missing)
        self.assertEqual(merged['values'][0], .125)
        self.assertEqual(merged['values'][1], .25)
        self.assertEqual(previous['values'], [.125] * 96)

    def test_gap_replay_reconciles_previous_rows_without_estimated_consumption(self):
        days = curve.parse_load_data(payload(day='2026-11-07'))
        days.update(curve.parse_load_data(payload()))
        stats = curve.hourly_energy(days, 'WI')
        self.assertEqual(len(stats), 73)
        # The missing day carries the running sum; it adds no energy.
        self.assertTrue(all(s['sum'] == 12 for s in stats[25:49]))
        self.assertEqual(stats[-1]['sum'], 24)
