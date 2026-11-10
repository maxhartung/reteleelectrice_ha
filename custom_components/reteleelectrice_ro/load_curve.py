"""Validate the detailed load service and build hourly energy statistics."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

PORTAL_ZONE = ZoneInfo('Europe/Bucharest')


def parse_load_data(payload: object) -> dict[str, dict]:
    """Keep all energy channels at the service's original granularity."""
    if not isinstance(payload, dict) or str(payload.get('result', '')).upper() != 'OK':
        raise ValueError('Detailed load service did not confirm success')
    rows = payload.get('ListData')
    if not isinstance(rows, list):
        raise ValueError('Detailed load response has no ListData')
    days: dict[str, dict] = {}
    for row in rows:
        day = date.fromisoformat(row['DateT']).isoformat()
        frequency = int(row['Frequency'])
        channel = str(row['ReadingType'])
        if frequency not in (15, 30, 60) or not isinstance(row['Value'], list):
            raise ValueError('Unsupported load interval')
        values = []
        for raw in row['Value']:
            value = None if raw is None or str(raw).strip() == '' else float(str(raw).replace(',', '.'))
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('Invalid load value')
            values.append(value)
        if len(values) > 1500 // frequency:
            raise ValueError('Too many intervals for one day')
        channels = days.setdefault(day, {})
        if channel in channels:
            raise ValueError('Duplicate load channel')
        channels[channel] = {'frequency': frequency, 'values': values}
    return days


def hourly_energy(days: dict[str, dict], channel: str) -> list[dict]:
    """Build cumulative sums from complete, unambiguous hours only.

    On DST days accept only an elapsed-time array matching the day's actual
    length. A fixed 96-slot export on a 23/25-hour day is ambiguous and retained
    as raw data only, rather than guessing where the clock change belongs.
    """
    hours = {}
    for key, channels in sorted(days.items()):
        row = channels.get(channel)
        if row is None:
            continue
        day = date.fromisoformat(key)
        start = datetime.combine(day, time.min, PORTAL_ZONE).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, PORTAL_ZONE).astimezone(timezone.utc)
        frequency = row['frequency']
        values = row['values']
        expected = int((end - start).total_seconds() / (frequency * 60))
        if len(values) != expected:
            continue
        per_hour = 60 // frequency
        for index in range(0, len(values), per_hour):
            batch = values[index:index + per_hour]
            if all(value is not None for value in batch):
                hours[start + timedelta(minutes=index * frequency)] = round(sum(batch), 6)
    if not hours:
        return []
    total = 0.0
    stats = [{'start': min(hours) - timedelta(hours=1), 'sum': 0.0}]
    # Re-emit every hour in the retained range, including gaps. Recorder only
    # upserts supplied timestamps: omitting an old synthetic baseline would
    # leave a stale zero sum when earlier history arrives. Carrying the known
    # cumulative sum across gaps adds no estimated consumption.
    stamp = min(hours)
    last_hour = max(hours)
    while stamp <= last_hour:
        total = round(total + hours.get(stamp, 0.0), 6)
        stats.append({'start': stamp, 'sum': total})
        stamp += timedelta(hours=1)
    return stats


def merge_load_row(day: str, previous: dict | None, fresh: dict) -> dict:
    """Preserve cached intervals when the portal returns a partial array.

    A changed interval count/frequency may change timestamp interpretation,
    especially around DST. Accept that replacement only for a complete day.
    """
    if previous is None:
        return fresh
    if previous['frequency'] == fresh['frequency'] and len(previous['values']) == len(fresh['values']):
        return dict(fresh, values=[new if new is not None else old
                    for old, new in zip(previous['values'], fresh['values'])])
    current_day = date.fromisoformat(day)
    start = datetime.combine(current_day, time.min, PORTAL_ZONE).astimezone(timezone.utc)
    end = datetime.combine(current_day + timedelta(days=1), time.min, PORTAL_ZONE).astimezone(timezone.utc)
    expected = int((end - start).total_seconds() / (fresh['frequency'] * 60))
    if len(fresh['values']) == expected and all(value is not None for value in fresh['values']):
        return fresh
    return previous
