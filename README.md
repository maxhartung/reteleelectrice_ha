# Rețele Electrice România for Home Assistant

An independent Home Assistant custom integration for reading electricity and smart-meter data from the Rețele Electrice România customer portal.

This project is in early development. It connects directly to the user's portal account; it does not use a license server, shared credential service, or central proxy.

## Initial scope

- Dynamic Salesforce Experience Cloud login and session bootstrap.
- POD discovery and meter metadata.
- Current smart-meter values.
- Monthly load-curve data at the portal's available granularity (currently
  15-minute data for smart meters), aggregated into hourly and daily values.
- Historical readings, outages, and the two-stage consumption-data workflow.

For each POD with curve data, the integration also creates:

- `Consum zilnic (curbă)`: total active consumption for the latest day supplied
  by the portal.
- `Consum ultima oră (curbă)`: the latest available hourly bucket.

Both sensors include `daily_consumption` and `hourly_consumption` attributes so
the values can be used in dashboards and automations. The portal may publish
curve data with a delay, so the latest day is not necessarily today.

## Instant-meter refresh automation

The integration creates an `Actualizare valori instantanee` button for every
POD. Pressing it runs the portal's two-step `ReqMeterInstantData` and
`FindOutMeterInstantData` workflow, then updates the smart-meter entities.

Example automations are in
[`examples/instant_refresh_automations.yaml`](examples/instant_refresh_automations.yaml).
Use the 15-minute or hourly schedule, not both at the same time. The portal can
limit how often instant values may be requested; if refreshes begin returning
errors, switch to the hourly schedule.

The portal is a private cloud service with an undocumented interface. Requests are deliberately conservative and the implementation must tolerate portal changes.

## Development

The pure load-curve parser can be tested without Home Assistant:

```bash
python3 -m unittest discover -s tests -v
```
