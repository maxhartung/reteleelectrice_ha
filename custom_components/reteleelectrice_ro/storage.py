"""Confirm quota writes instead of relying on Store's logged-only failures."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.storage import Store


class ConfirmedStore(Store):
    """Fail closed if Home Assistant suppresses or defers a write.

    Store.async_save logs serialization/write errors rather than propagating
    them. The write hook confirms completion only after the executor finishes.
    Coordinator updates serialize access to this store.
    """

    async def async_save(self, data: dict[str, Any]) -> None:
        self._write_completed = False
        await super().async_save(data)
        if not self._write_completed:
            raise OSError("Meter request history could not be persisted")

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        await super()._async_write_data(data)
        self._write_completed = True
