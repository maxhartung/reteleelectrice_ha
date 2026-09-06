"""Constants for the Rețele Electrice România integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "reteleelectrice_ro"
BASE_URL = "https://contulmeu.reteleelectrice.ro"
LOGIN_PAGE = f"{BASE_URL}/PEDRO_SiteLogin"
AURA_URL = f"{BASE_URL}/s/sfsites/aura"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)
ATTRIBUTION = "Data from contulmeu.reteleelectrice.ro"
VF_PAGE_MAP = {
    "ReqMeterInstantData": "PED_ProxyCallWSAsynSmartMeterIstantData",
    "FindOutMeterInstantData": "PED_ProxyCallWSAsynSmartMeterIstantData",
}
