# Rețele Electrice România for Home Assistant

Independent integration for the customer portal's instantaneous smart-meter data.

Each smart-meter POD exposes exactly four sensors:

- **Voltage (V)**: phase R, as reported by the meter.
- **Current (A)**: phase R, as reported by the meter.
- **Estimated power (kW)**: voltage × current ÷ 1,000, assuming power factor 1.
  This is an estimate; actual active power depends on power factor. It is not
  energy in kWh and must not be used as a measured energy source.
- **Website last update**: the portal's `LAST_UPDATED` timestamp, interpreted in
  Europe/Bucharest when no offset is supplied. It never uses HA's polling time.

The values describe the last measurement published by the website, not a live
measurement at the time you view the card.

## Automatic requests

No separate Home Assistant automation or button is needed. The integration
checks available results every five minutes and automatically submits
`ReqMeterInstantData` when eligible. `FindOutMeterInstantData` retrieves results
separately, so a pending request does not cause repeated submissions.

Requests are spaced at least two hours apart, with at most 10 attempts in any
rolling 24 hours per configured account. After the tenth request, submissions
pause until the oldest attempt leaves that window. Processing can take up to
two hours. Failed or ambiguous attempts also count, and submissions are not
retried automatically. The quota and last readings are saved across restarts.
Manual website requests are outside the integration's local counter and may
cause the portal to reject an otherwise eligible automatic request.

Empty or failed reads retain the previous reading; an old timestamp makes its
age visible. Partial readings do not combine voltage and current from different
measurements. Authentication expiry is handled through Home Assistant's
reauthentication flow.

## Upgrade and dashboard

Update with HACS, then restart Home Assistant. Obsolete integration entities are
removed from the entity registry on setup. Existing voltage/current entity IDs
are preserved. Historical recorder data is not purged.

Remove any old button-press automations. The integration no longer fetches
load curves, reading history, outages, supplier details, cumulative energy,
production, or average power. Only account/POD identifiers needed for the meter
API are retained internally.

Use an Entities card with the four sensors. See
[the card example](examples/smart_meter_card.yaml); substitute the IDs shown in
your Home Assistant installation.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests cover portal parsing, estimated power, timestamp handling, request quota
boundaries, and coordinator persistence/failure behavior.
