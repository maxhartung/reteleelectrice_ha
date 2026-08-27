# Rețele Electrice România for Home Assistant

An independent Home Assistant custom integration for reading electricity and smart-meter data from the Rețele Electrice România customer portal.

This project is in early development. It connects directly to the user's portal account; it does not use a license server, shared credential service, or central proxy.

## Initial scope

- Dynamic Salesforce Experience Cloud login and session bootstrap.
- POD discovery and meter metadata.
- Current smart-meter values.
- Monthly load-curve CSV parsing (`EA`, `EAP`, `ER`, and `ERC` at 15-minute resolution).
- Historical readings, outages, and the two-stage consumption-data workflow.

The portal is a private cloud service with an undocumented interface. Requests are deliberately conservative and the implementation must tolerate portal changes.

## Development

The pure load-curve parser can be tested without Home Assistant:

```bash
python3 -m unittest discover -s tests -v
```

