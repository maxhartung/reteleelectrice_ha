"""Parser for the Rețele Electrice monthly load-curve CSV export."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


class LoadCurveParseError(ValueError):
    """Raised when a load-curve export is malformed."""


@dataclass(frozen=True)
class LoadCurveDay:
    """One exported day of quarter-hour register data."""

    day: date
    frequency_minutes: int
    registers: dict[str, tuple[float | None, ...]]

    @property
    def interval_count(self) -> int:
        """Return the number of intervals in the export."""
        return max((len(values) for values in self.registers.values()), default=0)

    def total(self, register: str) -> float:
        """Return the sum of all available interval values for a register."""
        return round(
            sum(value for value in self.registers.get(register, ()) if value is not None),
            6,
        )

    def samples(self, register: str) -> tuple[tuple[datetime, float], ...]:
        """Return timestamped samples for one register."""
        values = self.registers.get(register, ())
        start = datetime.combine(self.day, time.min)
        return tuple(
            (start + timedelta(minutes=index * self.frequency_minutes), value)
            for index, value in enumerate(values)
            if value is not None
        )


def _parse_number(value: str) -> float | None:
    value = value.strip().strip('"')
    if not value:
        return None
    try:
        if "," in value:
            value = value.replace(".", "").replace(",", ".")
        return float(value)
    except ValueError as err:
        raise LoadCurveParseError(f"Invalid numeric value: {value!r}") from err


def parse_load_curve_csv(payload: str | bytes) -> LoadCurveDay:
    """Parse the semicolon-delimited CSV exported by the portal.

    The export contains one row per register and Q1..Q96 columns for a typical
    15-minute day. Decimal commas and quoted values are accepted.
    """
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig")
    else:
        payload = payload.lstrip("\ufeff")

    rows = list(csv.reader(io.StringIO(payload), delimiter=";", quotechar='"'))
    if not rows:
        raise LoadCurveParseError("CSV is empty")

    header = [cell.strip().strip('"') for cell in rows[0]]
    if len(header) < 4 or header[:3] != ["Zi", "Frecventa", "Marime"]:
        raise LoadCurveParseError("Unexpected load-curve header")

    interval_columns = header[3:]
    if not all(column.startswith("Q") for column in interval_columns):
        raise LoadCurveParseError("Load-curve interval columns must be Q1..Qn")

    parsed_day: date | None = None
    frequency: int | None = None
    registers: dict[str, tuple[float | None, ...]] = {}

    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))

        raw_day = row[0].strip().strip('"')
        try:
            current_day = date.fromisoformat(raw_day.replace(".", "-"))
        except ValueError as err:
            raise LoadCurveParseError(f"Invalid export date: {raw_day!r}") from err

        try:
            current_frequency = int(row[1].strip().strip('"'))
        except ValueError as err:
            raise LoadCurveParseError(f"Invalid frequency: {row[1]!r}") from err
        if current_frequency <= 0:
            raise LoadCurveParseError("Frequency must be positive")

        register = row[2].strip().strip('"')
        if not register:
            raise LoadCurveParseError("Register name is empty")
        if register in registers:
            raise LoadCurveParseError(f"Duplicate register: {register}")

        if parsed_day is None:
            parsed_day = current_day
            frequency = current_frequency
        elif parsed_day != current_day or frequency != current_frequency:
            raise LoadCurveParseError("Rows contain different dates or frequencies")

        registers[register] = tuple(_parse_number(value) for value in row[3 : len(header)])

    if parsed_day is None or frequency is None or not registers:
        raise LoadCurveParseError("CSV contains no data rows")

    return LoadCurveDay(parsed_day, frequency, registers)
