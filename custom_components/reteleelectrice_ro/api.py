"""Async client for the Rețele Electrice România customer portal."""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urljoin

import aiohttp

from .const import AURA_URL, BASE_URL, LOGIN_PAGE, VF_PAGE_MAP
from .load_curve import LoadCurveMonth, parse_load_curve_response


class PortalError(RuntimeError):
    """Base exception for portal failures."""


class AuthenticationError(PortalError):
    """Raised when portal authentication fails or expires."""


class PortalProtocolError(PortalError):
    """Raised when the portal response no longer matches expectations."""


@dataclass(slots=True)
class AuraBootstrap:
    """Runtime values extracted from the current Salesforce app shell."""

    fwuid: str
    app_uid: str
    token: str


def _attributes(tag: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in re.finditer(
        r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag
    ):
        key, double_quoted, single_quoted, unquoted = match.groups()
        attributes[key.lower()] = html.unescape(
            double_quoted or single_quoted or unquoted or ""
        )
    return attributes


def _form_details(page: str) -> tuple[str, str]:
    match = re.search(r"<form\b([^>]*)>", page, re.IGNORECASE)
    if not match:
        return "", ""
    attrs = _attributes(match.group(1))
    return attrs.get("id", ""), attrs.get("action", "")


def _form_fields(page: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", page, re.IGNORECASE):
        attrs = _attributes(tag)
        name = attrs.get("name")
        if name:
            fields[name] = attrs.get("value", "")
    return fields


def _find_field_name(page: str, candidates: tuple[str, ...]) -> str:
    input_tags = re.findall(r"<input\b[^>]*>", page, re.IGNORECASE)
    if "password" in candidates:
        for tag in input_tags:
            attrs = _attributes(tag)
            if attrs.get("type", "").lower() == "password" and attrs.get("name"):
                return attrs["name"]

    for tag in re.findall(r"<input\b[^>]*>", page, re.IGNORECASE):
        attrs = _attributes(tag)
        name = attrs.get("name", "")
        haystack = " ".join(
            attrs.get(attribute, "")
            for attribute in ("name", "id", "placeholder", "autocomplete")
        ).lower()
        if name and any(candidate.lower() in haystack for candidate in candidates):
            return name

    if "username" in candidates or "email" in candidates:
        for tag in input_tags:
            attrs = _attributes(tag)
            if attrs.get("type", "").lower() in {"email", "text"} and attrs.get("name"):
                return attrs["name"]
    return candidates[0]


def _submit_field(page: str) -> tuple[str, str] | None:
    for tag in re.findall(r"<input\b[^>]*>", page, re.IGNORECASE):
        attrs = _attributes(tag)
        if attrs.get("type", "").lower() in {"submit", "button"} and attrs.get("name"):
            return attrs["name"], attrs.get("value", "")

    # Salesforce Visualforce can submit through an anchor and inject the
    # submit field with jsfcljs() instead of rendering an input element.
    match = re.search(
        r"jsfcljs\([^,]+,\s*['\"]([^,'\"]+),([^'\"]*)['\"]",
        page,
        re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)
    return None


def _extract_frontdoor(page: str) -> str | None:
    match = re.search(
        r"(?:window\.location\.(?:replace|href)|handleRedirect)\s*(?:=|\()\s*['\"]([^'\"]*frontdoor\.jsp[^'\"]*)",
        page,
        re.IGNORECASE,
    )
    return html.unescape(match.group(1)) if match else None


def _json_or_text(payload: str) -> Any:
    cleaned = payload.lstrip()
    cleaned = re.sub(r"^(?:while\s*\(1\);|for\s*\(;;\);)", "", cleaned).lstrip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return payload


def _json_fragments(payload: str) -> list[Any]:
    """Extract JSON objects/arrays embedded in an A4J response."""
    decoder = json.JSONDecoder()
    fragments: list[Any] = []
    for source in (payload, html.unescape(payload)):
        for index, character in enumerate(source):
            if character not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(source[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, (dict, list)) and value not in fragments:
                fragments.append(value)
    return fragments


def _parse_vf_response(payload: str) -> Any:
    """Parse a Visualforce A4J response while preserving CSV/text responses."""
    direct = _json_or_text(payload)
    if not isinstance(direct, str):
        return direct

    # A4J usually wraps the actual web-service result in one or more CDATA
    # blocks inside an XML partial-page response.
    cdata_blocks = re.findall(r"<!\[CDATA\[(.*?)\]\]>", payload, re.DOTALL)
    for block in cdata_blocks:
        fragments = _json_fragments(block)
        if fragments:
            return fragments[0]
        if "Zi;Frecventa;Marime" in block[:200]:
            return html.unescape(block)

    fragments = _json_fragments(payload)
    if fragments:
        return fragments[0]

    # Some proxy methods return useful values as updated input elements rather
    # than JSON. Exclude the framework state fields from that fallback.
    values: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", payload, re.IGNORECASE):
        attrs = _attributes(tag)
        name = attrs.get("name") or attrs.get("id")
        if not name or name.startswith("com.salesforce.visualforce.ViewState"):
            continue
        if attrs.get("value"):
            values[name] = attrs["value"]
    return values or payload


def _runtime_configs(shell_html: str) -> list[dict[str, Any]]:
    """Decode Salesforce runtime JSON embedded in script URLs."""
    configs: list[dict[str, Any]] = []
    for encoded in re.findall(r"/s/sfsites/l/([^/]+)/(?:resources|app)\.js", shell_html):
        try:
            decoded = json.loads(unquote(encoded))
        except (json.JSONDecodeError, UnicodeError):
            continue
        if isinstance(decoded, dict):
            configs.append(decoded)
    return configs


class ReteleElectriceClient:
    """Minimal async portal client with dynamic Salesforce bootstrap."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self._session = session
        self._owns_session = session is None
        self._bootstrap: AuraBootstrap | None = None
        self._action_counter = 0
        self._logged_in = False

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in and self._bootstrap is not None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Home Assistant Rețele Electrice integration",
                    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
                }
            )
            self._owns_session = True
        return self._session

    async def async_close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def async_login(self) -> None:
        """Log in and bootstrap current Aura runtime values."""
        session = await self._get_session()
        login_url = f"{LOGIN_PAGE}?startURL=%2Fs%2F&refURL={BASE_URL}%2Fs%2F"

        async with session.get(login_url, allow_redirects=True) as response:
            if response.status != 200:
                raise AuthenticationError(f"Login page returned HTTP {response.status}")
            login_html = await response.text()
            login_page_url = str(response.url)

        form_id, form_action = _form_details(login_html)
        fields = _form_fields(login_html)
        username_name = _find_field_name(login_html, ("username", "email"))
        password_name = _find_field_name(login_html, ("password", "pw"))
        submit_field = _submit_field(login_html)
        fields.update(
            {
                form_id or "loginPage:loginForm": form_id or "loginPage:loginForm",
                username_name: self.email,
                password_name: self.password,
            }
        )
        if submit_field:
            fields[submit_field[0]] = submit_field[1]

        target = urljoin(login_page_url, form_action or login_url)
        async with session.post(
            target,
            data=fields,
            allow_redirects=False,
            headers={"Origin": BASE_URL, "Referer": login_page_url},
        ) as response:
            post_html = await response.text()
            if response.status in (401, 403):
                raise AuthenticationError("Portal rejected the credentials")
            redirect = response.headers.get("Location") or _extract_frontdoor(post_html)

        if not redirect:
            raise AuthenticationError("Portal login did not return a Salesforce redirect")
        async with session.get(urljoin(BASE_URL, redirect), allow_redirects=True) as response:
            if response.status >= 400:
                raise AuthenticationError(f"Salesforce redirect returned HTTP {response.status}")

        async with session.get(f"{BASE_URL}/s/", allow_redirects=True) as response:
            if response.status != 200:
                raise AuthenticationError(f"Portal shell returned HTTP {response.status}")
            shell_html = await response.text()

        self._bootstrap = self._extract_bootstrap(shell_html)
        self._logged_in = True

    def _extract_bootstrap(self, shell_html: str) -> AuraBootstrap:
        runtime_configs = _runtime_configs(shell_html)
        fwuid_match = re.search(r"/auraFW/javascript/([^/]+)/aura_prod\.js", shell_html)
        app_uid = ""
        fwuid = fwuid_match.group(1) if fwuid_match else ""
        for config in runtime_configs:
            fwuid = fwuid or str(config.get("fwuid") or "")
            loaded = config.get("loaded")
            if isinstance(loaded, dict):
                app_uid = app_uid or str(
                    loaded.get("APPLICATION@markup://siteforce:communityApp") or ""
                )

        plain_fwuid_match = re.search(r"[\"']fwuid[\"']\s*:\s*[\"']([^\"']+)", shell_html)
        plain_app_match = re.search(
            r"APPLICATION@markup://siteforce:communityApp[\"']\s*:\s*[\"']([^\"']+)",
            shell_html,
        )
        fwuid = fwuid or (plain_fwuid_match.group(1) if plain_fwuid_match else "")
        app_uid = app_uid or (plain_app_match.group(1) if plain_app_match else "")
        token_match = re.search(r"[\"']aura\.token[\"']\s*[:=]\s*[\"']([^\"']+)", shell_html)

        token = token_match.group(1) if token_match else ""
        if not token and self._session is not None:
            for cookie in self._session.cookie_jar:
                if "ERIC_PROD" in cookie.key.upper():
                    token = cookie.value
                    break

        if not fwuid or not app_uid or not token:
            raise PortalProtocolError("Could not bootstrap current Salesforce Aura metadata")
        return AuraBootstrap(fwuid, app_uid, token)

    async def _ensure_login(self) -> None:
        if not self.is_logged_in:
            await self.async_login()

    async def async_aura_call(
        self,
        descriptor: str,
        *,
        params: dict[str, Any] | None = None,
        calling_descriptor: str = "UNKNOWN",
    ) -> Any:
        await self._ensure_login()
        assert self._bootstrap is not None
        session = await self._get_session()
        self._action_counter += 1

        action = {
            "id": f"{self._action_counter};a",
            "descriptor": descriptor,
            "callingDescriptor": calling_descriptor,
            "params": params or {},
        }
        context = {
            "mode": "PROD",
            "fwuid": self._bootstrap.fwuid,
            "app": "siteforce:communityApp",
            "loaded": {
                "APPLICATION@markup://siteforce:communityApp": self._bootstrap.app_uid
            },
            "dn": [],
            "globals": {},
            "uad": True,
        }
        payload = {
            "message": json.dumps({"actions": [action]}, separators=(",", ":")),
            "aura.context": json.dumps(context, separators=(",", ":")),
            "aura.pageURI": "/s/",
            "aura.token": self._bootstrap.token,
        }

        async with session.post(AURA_URL, data=payload, headers={"Referer": f"{BASE_URL}/s/"}) as response:
            if response.status in (401, 403):
                self._logged_in = False
                raise AuthenticationError("Aura session expired")
            if response.status != 200:
                raise PortalError(f"Aura call returned HTTP {response.status}")
            result = _json_or_text(await response.text())

        if isinstance(result, dict) and result.get("actions"):
            action_result = result["actions"][0]
            if action_result.get("state") == "ERROR":
                raise PortalError(str(action_result.get("error", action_result)))
            return_value = action_result.get("returnValue", action_result)
            if isinstance(return_value, str):
                decoded = _json_or_text(return_value)
                if decoded is not return_value:
                    return decoded
            return return_value
        return result

    async def async_get_pods(self) -> Any:
        return await self.async_aura_call(
            "apex://PED_Utility/ACTION$getPODs",
            calling_descriptor="markup://c:PED_HomePage",
        )

    async def async_get_pod_details(self, pod_name: str) -> Any:
        return await self.async_aura_call(
            "apex://PED_POD_Details_Controller/ACTION$getUserDetailsPodInformation",
            params={"PodName": pod_name},
            calling_descriptor="markup://c:PED_POD_Details",
        )

    async def async_get_account_info(self) -> Any:
        return await self.async_aura_call(
            "apex://PED_Utility/ACTION$getAccountInfo",
            calling_descriptor="markup://c:PED_CustomProfileHeader",
        )

    async def _call_vf_ws(self, method_name: str, method_params: list[str]) -> Any:
        await self._ensure_login()
        page_name = VF_PAGE_MAP.get(method_name)
        if not page_name:
            raise PortalProtocolError(f"Unknown Visualforce method: {method_name}")
        session = await self._get_session()
        page_url = f"{BASE_URL}/{page_name}"

        async with session.get(page_url) as response:
            if response.status != 200:
                raise PortalError(f"Visualforce page returned HTTP {response.status}")
            page_html = await response.text()

        fields = _form_fields(page_html)
        form_id, form_action = _form_details(page_html)
        fields.update(
            {
                "AJAXREQUEST": "_viewRoot",
                form_id or "j_id0:j_id2": form_id or "j_id0:j_id2",
                "methodN": method_name,
                "params": ",".join(str(value) for value in method_params),
                "uniqueId": f"script_{int(time.time())}",
            }
        )
        action_match = re.search(
            r"similarityGroupingId['\"]\s*:\s*['\"]([^'\"]+)", page_html
        )
        action_id = action_match.group(1) if action_match else f"{form_id}:j_id3"
        fields[action_id] = action_id
        target = urljoin(page_url, form_action or f"/{page_name}")

        async with session.post(target, data=fields, headers={"Referer": page_url}) as response:
            if response.status in (401, 403):
                self._logged_in = False
                raise AuthenticationError("Visualforce session expired")
            if response.status != 200:
                raise PortalError(f"Visualforce call returned HTTP {response.status}")
            return _parse_vf_response(await response.text())

    async def async_get_power_outages(self, pod_name: str) -> Any:
        return await self._call_vf_ws("PowerOutages", [pod_name, "RO"])

    async def async_get_smart_meter_current(self, pod_name: str, cnp: str = "") -> Any:
        if not cnp:
            account = await self.async_get_account_info()
            if isinstance(account, dict):
                cnp = str(account.get("CNP__c") or account.get("Fiscal_Code__c") or "")
        return await self._call_vf_ws("FindOutMeterCurrentData", [cnp, "", pod_name])

    async def async_get_instant_values(self, pod_name: str, cnp: str = "") -> Any:
        params = [cnp, "", pod_name]
        await self._call_vf_ws("ReqMeterInstantData", params)
        return await self._call_vf_ws("FindOutMeterInstantData", params)

    async def async_get_load_curve(
        self,
        pod_name: str,
        year: int,
        month: int,
        energy_type: str = "WI",
    ) -> LoadCurveMonth:
        """Fetch one month's active-consumption curve.

        ``WI`` is the portal's code for consumed active energy. The live
        portal sends the POD, energy type, start date, and end date in this
        order to ``CurveDiCaricoGraph``.
        """
        import calendar

        if not 1 <= month <= 12:
            raise PortalProtocolError(f"Invalid curve month: {month}")
        last_day = calendar.monthrange(year, month)[1]
        method_params = [
            pod_name,
            energy_type,
            f"01/{month:02d}/{year} 00:00:00",
            f"{last_day:02d}/{month:02d}/{year} 23:59:59",
        ]
        result = await self._call_vf_ws("CurveDiCaricoGraph", method_params)
        try:
            return parse_load_curve_response(result)
        except ValueError as err:
            raise PortalProtocolError("Load-curve response could not be parsed") from err

    async def async_get_load_curve_csv(
        self, method_params: list[str]
    ) -> LoadCurveMonth:
        """Compatibility wrapper for callers with captured portal parameters."""
        result = await self._call_vf_ws("CurveDiCaricoGraph", method_params)
        try:
            return parse_load_curve_response(result)
        except ValueError as err:
            raise PortalProtocolError("Load-curve response could not be parsed") from err
