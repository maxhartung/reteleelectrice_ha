"""Constants for the Rețele Electrice România integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "reteleelectrice_ro"
BASE_URL = "https://contulmeu.reteleelectrice.ro"
LOGIN_PAGE = f"{BASE_URL}/PEDRO_SiteLogin"
AURA_URL = f"{BASE_URL}/s/sfsites/aura"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_PODS = "pods"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_UPDATE_INTERVAL = timedelta(hours=1)
MIN_UPDATE_INTERVAL = timedelta(minutes=15)
MAX_UPDATE_INTERVAL = timedelta(hours=24)
INSTANT_REQUEST_MIN_INTERVAL = timedelta(minutes=15)

ATTRIBUTION = "Data from contulmeu.reteleelectrice.ro"

VF_PAGE_MAP: dict[str, str] = {
    "CurveDiCaricoGraph": "PED_ProxyCallWSAsync_Curve_VF",
    "RetriveSingleSelf": "PED_ProxyCallWSAsynSingleSelf_VF",
    "PowerOutages": "PED_ProxyCallWSAsynPowerOutages_VF",
    "FindOutMeterHistoryData": "PED_ProxyCallWSAsync_SmartMeter_Vf",
    "FindOutMeterCurrentData": "PED_ProxyCallWSAsynSmartMeterCurrentData",
    "ReqMeterInstantData": "PED_ProxyCallWSAsynSmartMeterIstantData",
    "FindOutMeterInstantData": "PED_ProxyCallWSAsynSmartMeterIstantData",
    "queryPOD": "PED_ProxyCallWSAsync_Curve_VF",
}
