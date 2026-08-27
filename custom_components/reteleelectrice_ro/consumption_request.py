"""State machine for the portal's delayed two-hour consumption request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


REQUEST_LIMIT = 10
REQUEST_WINDOW = timedelta(hours=24)
REQUEST_TIMEOUT = timedelta(hours=2)


@dataclass
class ConsumptionRequestState:
    """Persistable state for one POD's delayed data request."""

    status: str = "idle"
    requested_at: datetime | None = None
    ready_at: datetime | None = None
    requests_in_window: int = 0
    window_started_at: datetime | None = None

    def _now(self, now: datetime | None) -> datetime:
        return now or datetime.now(timezone.utc)

    def _reset_window_if_needed(self, now: datetime) -> None:
        if self.window_started_at and now - self.window_started_at >= REQUEST_WINDOW:
            self.requests_in_window = 0
            self.window_started_at = now

    def can_request(self, now: datetime | None = None) -> bool:
        """Return whether another request may be submitted."""
        current = self._now(now)
        self._reset_window_if_needed(current)
        return self.status not in {"requested", "processing"} and self.requests_in_window < REQUEST_LIMIT

    def mark_requested(self, now: datetime | None = None) -> None:
        """Record a request submitted to the portal."""
        current = self._now(now)
        if not self.can_request(current):
            raise RuntimeError("Consumption-data request is not currently allowed")
        self.status = "requested"
        self.requested_at = current
        self.ready_at = None
        self.requests_in_window += 1
        self.window_started_at = self.window_started_at or current

    def mark_processing(self) -> None:
        """Mark the portal job as accepted but not ready."""
        if self.status == "requested":
            self.status = "processing"

    def mark_ready(self, now: datetime | None = None) -> None:
        """Mark the result as ready for retrieval."""
        self.status = "ready"
        self.ready_at = self._now(now)

    def mark_failed(self) -> None:
        """Mark a portal-reported failure."""
        self.status = "failed"

    def expire_if_needed(self, now: datetime | None = None) -> bool:
        """Expire a request that has exceeded the portal's two-hour window."""
        current = self._now(now)
        if self.requested_at and current - self.requested_at >= REQUEST_TIMEOUT and self.status in {"requested", "processing"}:
            self.status = "expired"
            return True
        return False
