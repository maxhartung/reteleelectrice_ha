"""Quota boundaries, rolling windows, restarts, and ambiguous failures."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'custom_components/reteleelectrice_ro/consumption_request.py'
spec = importlib.util.spec_from_file_location('request_state', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
State = module.ConsumptionRequestState


class ConsumptionRequestTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 6, tzinfo=timezone.utc)

    def test_two_hour_cooldown(self):
        state = State()
        state.mark_requested(self.start)
        self.assertFalse(state.can_request(self.start + timedelta(hours=2, seconds=-1)))
        self.assertTrue(state.can_request(self.start + timedelta(hours=2)))

    def test_ten_attempts_then_wait_until_rolling_window_expires(self):
        state = State()
        for i in range(10):
            state.mark_requested(self.start + timedelta(hours=2*i))
        for hours in (20, 22, 23.999):
            self.assertFalse(state.can_request(self.start + timedelta(hours=hours)))
        state.mark_requested(self.start + timedelta(hours=24))
        self.assertEqual(len(state.attempts), 10)
        self.assertFalse(state.can_request(self.start + timedelta(hours=25)))
        self.assertTrue(state.can_request(self.start + timedelta(hours=26)))

    def test_restart_keeps_failed_attempt_counted(self):
        state = State()
        state.mark_requested(self.start)
        restored = State(state.as_list())
        self.assertFalse(restored.can_request(self.start + timedelta(minutes=10)))
        with self.assertRaises(RuntimeError):
            restored.mark_requested(self.start + timedelta(minutes=10))

    def test_clock_moving_back_cannot_bypass_quota(self):
        state = State()
        state.mark_requested(self.start)
        self.assertFalse(state.can_request(self.start - timedelta(hours=1)))

    def test_reject_naive_persisted_timestamps(self):
        with self.assertRaises(ValueError):
            State(['2026-09-06T12:00:00'])
