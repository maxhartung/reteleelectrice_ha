# Rețele Electrice România for Home Assistant

Independent integration for the customer portal's instantaneous smart-meter data.

The overview card shows four smart-meter values:

- **Voltage (V)**: phase R, as reported by the meter.
- **Current (A)**: phase R, as reported by the meter.
- **Estimated power (kW)**: voltage × current ÷ 1,000, assuming power factor 1.
  This is an estimate; actual active power depends on power factor. It is not
  energy in kWh and must not be used as a measured energy source.
- **Website last update**: the portal's `LAST_UPDATED` timestamp, interpreted in
  Europe/Bucharest when no offset is supplied. It never uses HA's polling time.

The values describe the last measurement published by the website, not a live
measurement at the time you view the card.

## Energy dashboard

A fifth sensor, **Grid energy imported (kWh)**, reads the meter's cumulative
`Energia Activa 1.8.0` / `EA` register from the same website response. It has
`device_class: energy`, `state_class: total_increasing`, and unit `kWh`, making
it eligible for **Energy imported from grid** in the Energy dashboard.
The overview card still shows only its four display values.

Select this sensor for energy imports, not **Estimated power**, which measures
kW. Home Assistant calculates energy use from changes in the cumulative
register; the initial meter total is a baseline, not consumption for today.
Readings arrive with the portal's delay, so usage is recorded when HA receives
them, not reconstructed at the meter's original measurement time.

[Home Assistant's Energy troubleshooting guide](https://www.home-assistant.io/docs/energy/faq/#troubleshooting-missing-entities)
describes the required attributes and statistics checks. Old removed sensors
may still appear as historical statistics; use the new Grid energy imported
sensor rather than an old entity without a state.

## Automatic requests

No separate Home Assistant automation or button is needed. The integration
checks available results every five minutes and automatically submits
`ReqMeterInstantData` when eligible. `FindOutMeterInstantData` retrieves results
separately, so a pending request does not cause repeated submissions.

Requests are spaced at least two hours apart, with at most 10 attempts in any
rolling 24 hours per configured account. After the tenth request, submissions
pause until the oldest attempt leaves that window. Processing can take up to
two hours. Failed or ambiguous attempts also count, and submissions are not
retried automatically. Request rejection is logged, and the quota and last
complete readings are saved across restarts. Requests are blocked if an atomic
quota write fails or is deferred.
Manual website requests are outside the integration's local counter and may
cause the portal to reject an otherwise eligible automatic request.

Empty or failed reads retain the previous reading; an old timestamp makes its
age visible. Partial readings do not combine voltage and current from different
measurements. An empty initial POD discovery is retried. Expired sessions are
renewed automatically with saved credentials, including after a submission
fails authentication; the submission is never replayed. Temporary login-server
failures are retried later. Invalid credentials require Home Assistant's
reauthentication flow.

Each configured account has its own cookie session. Salesforce session-error
responses and login redirects trigger one renewal for reads. If the renewed
session is also rejected, polling retries later instead of requiring a new
password. A rejected login still starts Home Assistant's reauthentication flow.
If a previously working integration loses its values after several days,
session recovery also handles empty account responses and unusable Aura
metadata. Empty POD discovery renews the login and retries once before failing
the poll. No Home Assistant restart is needed to trigger those recovery paths.

## Troubleshooting unavailable sensors

Waiting several days will not resolve a rejected login or a portal response
that cannot be parsed. Check **Settings → Devices & services → Rețele Electrice
România** for a setup or reauthentication error, then **Settings → System →
Logs** for this integration's messages.

Download diagnostics from the integration entry's menu. In version 1.0.0,
`polling` includes the last poll time, session initialization flag, request
attempt times, and per-meter outcomes. `session_expired` means the portal
rejected the renewed session; `authentication_failed` means login failed;
`connection_failed` means discovery could not reach the portal.
`no_complete_reading` means the result did not contain a complete meter
snapshot. Diagnostics omit cookies, tokens, POD identifiers, and raw portal
responses. The session initialization flag describes local login state; it
does not guarantee the server still accepts that session.

If only the Energy dashboard is missing data while the meter entities have
values, check that **Grid energy imported** is selected and has a numeric kWh
value. It needs a baseline and a later increasing reading before HA can
calculate consumption.

## Upgrade and dashboard

Update with HACS, then restart Home Assistant. Obsolete integration entities are
removed from the entity registry on setup. Existing voltage/current entity IDs
are preserved. Historical recorder data is not purged.

Remove any old button-press automations. The integration no longer fetches
load curves, reading history, outages, supplier details,
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
