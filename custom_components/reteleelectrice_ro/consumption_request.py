"""Persistable rolling limit for meter-request attempts (including failures)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

REQUEST_LIMIT = 10
REQUEST_WINDOW = timedelta(hours=24)
REQUEST_INTERVAL = timedelta(hours=2)


class ConsumptionRequestState:
    """Share a conservative quota across all PODs on one account."""

    def __init__(self, attempts: list[str] | None = None) -> None:
        self.attempts = [datetime.fromisoformat(value) for value in (attempts or [])]
        if any(value.tzinfo is None for value in self.attempts):
            raise ValueError("Request history must contain timezone-aware timestamps")
        self.attempts.sort()

    def can_request(self, now: datetime) -> bool:
        recent = [value for value in self.attempts if now - value < REQUEST_WINDOW]
        return len(recent) < REQUEST_LIMIT and (
            not self.attempts or now - self.attempts[-1] >= REQUEST_INTERVAL
        )

    def mark_requested(self, now: datetime) -> None:
        if not self.can_request(now):
            raise RuntimeError("Meter request quota or cooldown has not elapsed")
        self.attempts = [value for value in self.attempts if now - value < REQUEST_WINDOW]
        self.attempts.append(now.astimezone(timezone.utc))

    def as_list(self) -> list[str]:
        return [value.isoformat() for value in self.attempts]
