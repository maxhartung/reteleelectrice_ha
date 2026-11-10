# Rețele Electrice România for Home Assistant

Independent integration for the customer portal's smart-meter readings and detailed energy history.

Version 1.2.0 requires **Home Assistant 2026.11.0 or newer**.

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

## Detailed energy backfill

Version 1.1.0 automatically reads `FindOutMeterLoadData` for the previous 31
calendar days, ending yesterday in Europe/Bucharest. It refreshes every six
hours to pick up delayed publications and corrections, independently of the
instant-meter request quota. On startup, cached curves are imported before
login. The first refresh fetches fresh curves before any instant-meter
submission, including when the instant-meter quota is exhausted.

Original interval readings for all returned energy channels are retained in
Home Assistant's integration storage across restarts. Complete hourly import
and export totals are published as external long-term statistics:

- `reteleelectrice_ro:<pod>_detailed_import`
- `reteleelectrice_ro:<pod>_detailed_export`

After updating and restarting Home Assistant, select **<POD> detailed grid
energy import** under Energy dashboard → Energy imported from grid. Replace
the cumulative meter sensor selection for that POD; selecting both counts the
same energy twice. Export statistics can be selected for return to grid.
These historical statistics do not create backdated sensor states. Today
becomes available after the portal publishes its data, normally tomorrow.

The portal's maximum granularity is typically 15 minutes. Home Assistant's
external statistics import uses hourly totals. Only complete hours contribute
energy. Across missing hours, the cumulative sum carries forward with no
estimated consumption; the dashboard therefore undercounts until those
readings become available. Replaying every hour also repairs old baseline
values when earlier readings arrive. A shorter or incompatible partial
response cannot replace a complete cached day.

On daylight-saving transition days, a fixed 96-slot export does not identify
the missing/repeated hour. Those intervals are retained in raw storage but
contribute no energy to statistics. Proper 92/100-slot arrays can be imported.
This can leave clock-change days undercounted.

Downloaded history is retained beyond the portal's rolling window, and
replayed with stable cumulative sums on restart. Corrections replace the
same timestamps rather than adding energy a second time. Diagnostics include
`backfill_statuses`; a detailed-data failure preserves the existing readings.

## Estimated monthly cost

Version 1.2.0 adds `reteleelectrice_ro:<pod>_detailed_import_cost` statistics in
RON. The default estimate uses **1.06 RON/kWh**. This is a user-supplied estimate,
not a tariff verified with your supplier. To change it, go to **Settings →
Devices & services → Rețele Electrice România → Configure**, enter your total
per-kWh price including VAT and applicable charges, and save. The integration
reloads and recalculates estimated costs from cached energy history.

The flat price applies to **all retained history**; changing it reprices past
estimates too. Time-varying tariffs, fixed fees, export compensation, and invoice
adjustments are not calculated. Published consumption normally ends yesterday,
and missing intervals undercount the estimate until they are available.

On your dashboard, select **Edit → Add card → Manual**, then paste:

```yaml
type: statistic
entity: reteleelectrice_ro:ro001e110948168_detailed_import_cost
name: Estimated electricity cost this month
icon: mdi:cash
stat_type: change
period:
  calendar:
    period: month
    offset: 0
```

Replace the POD with your own, in lowercase. See also
[the card example](examples/monthly_cost_card.yaml). The native
[Statistic card](https://www.home-assistant.io/dashboards/statistic/) accepts
external statistics and automatically follows the current calendar month.

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
failures, including HTTP 200 maintenance pages without a login redirect, are
retried later. Explicit credential rejections require Home Assistant's
reauthentication flow. Empty account identifiers renew the session once and
remain eligible for discovery on later polls; they are never cached or used
to submit quota-counted requests.

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

On Home Assistant 2026.11+, use **Settings → Marketplace** to install or update
this integration. HACS installations migrate automatically to the built-in
Marketplace. For a custom repository, use **Add from link** with
`https://github.com/maxhartung/reteleelectrice_ha`, then install the integration.
After an update, restart Home Assistant when prompted.

The repository keeps `hacs.json`, including its minimum Home Assistant version,
as required by Marketplace. It installs directly from `custom_components/`;
there is no release ZIP asset. Older HA installations should retain an older
compatible integration release.

[Marketplace documentation](https://www.home-assistant.io/integrations/marketplace/)
explains installation and migration. The external energy statistics use
`mean_type` and `unit_class` explicitly, matching the
[current recorder API](https://contributors.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/).
The Energy dashboard's new power-to-energy setup helper is unnecessary for
these measured kWh curves; select the detailed import/export statistics for
grid flows. The whole-home consumption option is for a whole-home meter,
which can differ from grid import when solar or batteries are present.

Obsolete integration entities are
removed from the entity registry on setup. Existing voltage/current entity IDs
are preserved. Historical recorder data is not purged.

Remove any old button-press automations. The integration fetches instant
readings and detailed energy curves. It does not fetch reading archives,
outages, supplier details, or average power.

Use an Entities card with the four sensors. See
[the card example](examples/smart_meter_card.yaml); substitute the IDs shown in
your Home Assistant installation.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests cover portal parsing, estimated power, timestamp handling, request quota
boundaries, and coordinator persistence/failure behavior.
