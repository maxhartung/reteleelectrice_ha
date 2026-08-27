"""Parsing and aggregation helpers for the Rețele Electrice load curves."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping


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

    def hourly_totals(self, register: str = "EA") -> tuple[float | None, ...]:
        """Aggregate interval values into 24 one-hour buckets.

        The portal currently exports 15-minute values for a smart meter, but
        this also accepts hourly and 30-minute exports. A bucket is ``None``
        when it contains no value at all, which lets Home Assistant
        distinguish a missing reading from a measured zero.
        """
        buckets: list[float | None] = [None] * 24
        for index, value in enumerate(self.registers.get(register, ())):
            if value is None:
                continue
            hour = (index * self.frequency_minutes) // 60
            if 0 <= hour < len(buckets):
                buckets[hour] = (buckets[hour] or 0.0) + value
        return tuple(round(value, 6) if value is not None else None for value in buckets)


@dataclass(frozen=True)
class LoadCurveMonth:
    """A collection of daily curves returned for one portal month."""

    days: tuple[LoadCurveDay, ...]

    @property
    def latest_day(self) -> LoadCurveDay | None:
        """Return the chronologically latest day with curve data."""
        return max(self.days, key=lambda item: item.day, default=None)

    def day(self, target: date) -> LoadCurveDay | None:
        """Return the curve for one date, if the portal supplied it."""
        return next((item for item in self.days if item.day == target), None)

    def daily_totals(self, register: str = "EA") -> dict[str, float]:
        """Return daily totals keyed by ISO date for Home Assistant attributes."""
        return {item.day.isoformat(): item.total(register) for item in self.days}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().strip('"')
    for candidate in (text, text[:10]):
        for normalised in (
            candidate.replace(".", "-"),
            candidate.replace("/", "-"),
        ):
            try:
                return date.fromisoformat(normalised)
            except ValueError:
                continue
    for pattern in (r"(\d{2})\.(\d{2})\.(\d{4})", r"(\d{2})/(\d{2})/(\d{4})"):
        match = re.search(pattern, text)
        if match:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    return None


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


def _parse_load_curve_csv_days(payload: str | bytes) -> tuple[LoadCurveDay, ...]:
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

    grouped: dict[date, dict[str, Any]] = {}

    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))

        raw_day = row[0].strip().strip('"')
        current_day = _parse_date(raw_day)
        if current_day is None:
            raise LoadCurveParseError(f"Invalid export date: {raw_day!r}")

        try:
            current_frequency = int(row[1].strip().strip('"'))
        except ValueError as err:
            raise LoadCurveParseError(f"Invalid frequency: {row[1]!r}") from err
        if current_frequency <= 0:
            raise LoadCurveParseError("Frequency must be positive")

        register = row[2].strip().strip('"')
        if not register:
            raise LoadCurveParseError("Register name is empty")

        day_data = grouped.setdefault(
            current_day,
            {"frequency": current_frequency, "registers": {}},
        )
        if day_data["frequency"] != current_frequency:
            raise LoadCurveParseError("Rows contain different frequencies for one date")
        registers = day_data["registers"]
        if register in registers:
            raise LoadCurveParseError(f"Duplicate register: {register}")
        registers[register] = tuple(
            _parse_number(value) for value in row[3 : len(header)]
        )

    if not grouped:
        raise LoadCurveParseError("CSV contains no data rows")

    return tuple(
        LoadCurveDay(day, values["frequency"], values["registers"])
        for day, values in sorted(grouped.items())
    )


def parse_load_curve_csv(payload: str | bytes) -> LoadCurveDay:
    """Parse a single-day semicolon-delimited load-curve CSV export."""
    days = _parse_load_curve_csv_days(payload)
    if len(days) != 1:
        raise LoadCurveParseError("CSV contains more than one day")
    return days[0]


def parse_load_curve_csv_month(payload: str | bytes) -> LoadCurveMonth:
    """Parse a monthly CSV export containing one or more daily rows."""
    return LoadCurveMonth(_parse_load_curve_csv_days(payload))


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return _parse_number(value)
    except LoadCurveParseError:
        return None


def _first_value(mapping: Mapping[str, Any], names: Iterable[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _frequency_for(values: list[float | None], explicit: Any = None) -> int:
    try:
        frequency = int(str(explicit).strip())
    except (TypeError, ValueError):
        frequency = 0
    if frequency > 0:
        return frequency
    if len(values) >= 96:
        return 15
    if len(values) >= 48:
        return 30
    return 60


def _values_from_mapping(mapping: Mapping[str, Any]) -> list[float | None] | None:
    """Extract interval values from common portal JSON shapes."""
    q_values: dict[int, float | None] = {}
    hour_values: dict[int, float | None] = {}
    for key, raw_value in mapping.items():
        text = str(key).strip()
        q_match = re.fullmatch(r"Q(\d+)", text, re.IGNORECASE)
        if q_match:
            q_values[int(q_match.group(1)) - 1] = _numeric(raw_value)
            continue
        hour_match = re.match(r"(\d{1,2}):00\s*-", text)
        if hour_match:
            hour_values[int(hour_match.group(1))] = _numeric(raw_value)
    if q_values:
        return [q_values.get(index) for index in range(max(q_values) + 1)]
    if hour_values:
        return [hour_values.get(index) for index in range(max(hour_values) + 1)]
    return None


def _point_from_mapping(mapping: Mapping[str, Any]) -> tuple[int, float | None] | None:
    """Extract one hourly point from a sample-object representation."""
    raw_index = _first_value(
        mapping,
        ("hour", "hourIndex", "sampleHour", "sampleIndex", "position", "index"),
    )
    raw_value = _first_value(
        mapping,
        ("value", "sampleValue", "reading", "energyValue", "hourValue", "consumption"),
    )
    if raw_index is None or raw_value is None:
        return None
    try:
        index = int(str(raw_index).strip())
    except ValueError:
        return None
    if not 0 <= index < 24:
        return None
    value = _numeric(raw_value)
    if value is None:
        return None
    return index, value


def _structured_rows(value: Any, inherited_day: date | None = None) -> list[tuple[date, int, str, list[float | None]]]:
    """Find dated interval arrays in a portal JSON response.

    Salesforce proxy payloads have changed shape between portal releases. The
    current UI has used both row objects and date-keyed dictionaries, so this
    intentionally accepts a small family of equivalent representations.
    """
    rows: list[tuple[date, int, str, list[float | None]]] = []
    if isinstance(value, list):
        values = [_numeric(item) for item in value]
        if inherited_day is not None and any(item is not None for item in values):
            rows.append((inherited_day, _frequency_for(values), "EA", values))
            return rows
        for item in value:
            rows.extend(_structured_rows(item, inherited_day))
        return rows
    if not isinstance(value, dict):
        return rows

    current_day = _parse_date(
        _first_value(
            value,
            ("day", "date", "zi", "sampleDate", "sample_date", "readingDate", "READING_DATE"),
        )
    ) or inherited_day
    register_value = _first_value(
        value, ("register", "energyType", "ENERGY_TYPE", "marime", "type", "code")
    )
    register = str(register_value or "EA").upper()
    if register in {"WI", "ACTIVE_CONSUMED", "ACTIVE_CONSUMPTION"}:
        register = "EA"
    explicit_frequency = _first_value(
        value, ("frequency", "frequencyMinutes", "Frecventa", "interval")
    )

    direct_values = _values_from_mapping(value)
    if direct_values is not None and current_day is not None:
        rows.append(
            (current_day, _frequency_for(direct_values, explicit_frequency), register, direct_values)
        )
    point = _point_from_mapping(value)
    if point and current_day is not None:
        point_values: list[float | None] = [None] * (point[0] + 1)
        point_values[point[0]] = point[1]
        rows.append((current_day, _frequency_for(point_values, explicit_frequency), register, point_values))

    for key in (
        "values",
        "readings",
        "samples",
        "hourlyValues",
        "intervals",
        "curve",
        "data",
        "sampleValueList",
        "sampleValues",
        "dataList",
        "curveData",
        "curveDataList",
        "dailyData",
        "hourlyData",
    ):
        nested = _first_value(value, (key,))
        if isinstance(nested, list):
            values = [_numeric(item) for item in nested]
            if current_day is not None and any(item is not None for item in values):
                rows.append(
                    (current_day, _frequency_for(values, explicit_frequency), register, values)
                )
            else:
                rows.extend(_structured_rows(nested, current_day))
        elif isinstance(nested, dict):
            rows.extend(_structured_rows(nested, current_day))

    for key, nested in value.items():
        if str(key).lower() in {
            "day", "date", "zi", "sampledate", "sample_date", "readingdate", "energytype", "register",
            "marime", "frequency", "frequencyminutes", "frecventa", "interval",
            "values", "readings", "samples", "hourlyvalues", "intervals", "curve", "data",
            "samplevaluelist", "samplevalues", "datalist", "curvedata", "curvedatalist",
            "dailydata", "hourlydata",
            "type", "code", "result", "errormessage",
        }:
            continue
        nested_day = _parse_date(key) or current_day
        if isinstance(nested, (dict, list)):
            rows.extend(_structured_rows(nested, nested_day))
    return rows


def _merge_rows(rows: Iterable[tuple[date, int, str, list[float | None]]]) -> LoadCurveMonth:
    grouped: dict[tuple[date, int], dict[str, list[float | None]]] = {}
    for day, frequency, register, values in rows:
        registers = grouped.setdefault((day, frequency), {})
        if register in registers:
            # Merge rows when the portal exposes one object per hour; also
            # tolerate the same data appearing through wrapper and nested keys.
            merged = registers[register]
            if len(values) > len(merged):
                merged.extend([None] * (len(values) - len(merged)))
            for index, item in enumerate(values):
                if item is not None:
                    merged[index] = item
        else:
            registers[register] = list(values)
    return LoadCurveMonth(
        tuple(
            LoadCurveDay(day, frequency, {key: tuple(values) for key, values in registers.items()})
            for (day, frequency), registers in sorted(grouped.items())
        )
    )


def parse_load_curve_response(payload: Any) -> LoadCurveMonth:
    """Parse the CSV or JSON response used by the monthly curve page."""
    if isinstance(payload, LoadCurveMonth):
        return payload
    if isinstance(payload, LoadCurveDay):
        return LoadCurveMonth((payload,))
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig")
    if isinstance(payload, str):
        text = payload.lstrip("\ufeff")
        if "Zi;Frecventa;Marime" in text[:200]:
            return parse_load_curve_csv_month(text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise LoadCurveParseError("Load-curve response is neither CSV nor JSON") from err
    if isinstance(payload, dict):
        result = str(payload.get("Result", "OK")).upper()
        if result not in {"OK", "SUCCESS", "TRUE"}:
            raise LoadCurveParseError(str(payload.get("ErrorMessage") or "Portal returned an error"))
        for nested in payload.values():
            if isinstance(nested, (str, bytes)):
                try:
                    return parse_load_curve_response(nested)
                except LoadCurveParseError:
                    continue
    parsed = _merge_rows(_structured_rows(payload))
    if not parsed.days:
        raise LoadCurveParseError("Load-curve JSON contains no dated interval data")
    return parsed
