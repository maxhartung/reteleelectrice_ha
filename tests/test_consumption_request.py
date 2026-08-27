"""Tests for the delayed consumption request state machine."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "reteleelectrice_ro" / "consumption_request.py"
MODULE_SPEC = importlib.util.spec_from_file_location("reteleelectrice_consumption_request", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = MODULE
MODULE_SPEC.loader.exec_module(MODULE)
REQUEST_LIMIT = MODULE.REQUEST_LIMIT
ConsumptionRequestState = MODULE.ConsumptionRequestState


class ConsumptionRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 8, 27, 10, tzinfo=timezone.utc)

    def test_request_moves_through_processing_and_ready(self) -> None:
        state = ConsumptionRequestState()
        state.mark_requested(self.start)
        state.mark_processing()
        self.assertEqual(state.status, "processing")
        state.mark_ready(self.start + timedelta(minutes=30))
        self.assertEqual(state.status, "ready")

    def test_request_expires_after_two_hours(self) -> None:
        state = ConsumptionRequestState()
        state.mark_requested(self.start)
        self.assertTrue(state.expire_if_needed(self.start + timedelta(hours=2)))
        self.assertEqual(state.status, "expired")

    def test_request_limit_is_enforced(self) -> None:
        state = ConsumptionRequestState()
        for index in range(REQUEST_LIMIT):
            state.status = "ready"
            state.mark_requested(self.start + timedelta(hours=index))
        state.status = "ready"
        self.assertFalse(state.can_request(self.start + timedelta(hours=23)))


if __name__ == "__main__":
    unittest.main()
