"""Config flow for Alexa Room Sync."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AlexaApiError, AlexaAuthError, AlexaRoomApi
from .auth import alexa_media_accounts, get_alexa_media_login
from .const import (
    AUTH_ALEXA_MEDIA,
    AUTH_COOKIE,
    CONF_ALEXA_MEDIA_ENTRY_ID,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_ENDPOINT_MODELS_JSON,
    CONF_HEADERS_JSON,
    CONF_HOST,
    CONF_MANUAL_MAPPINGS_JSON,
    DEFAULT_HOST,
    DEFAULT_ENDPOINT_MODELS_JSON,
    DOMAIN,
)
from .runtime import parse_string_list, parse_string_mapping

ALLOWED_SUFFIXES = (
    ".amazon.com",
    ".amazon.co.uk",
    ".amazon.de",
    ".amazon.it",
    ".amazon.es",
    ".amazon.fr",
    ".amazon.co.jp",
)


def _valid_host(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme == "https"
        and not parsed.path.strip("/")
        and any(hostname.endswith(suffix) for suffix in ALLOWED_SUFFIXES)
    )


def _parse_headers(raw: str) -> dict[str, str]:
    value = parse_string_mapping(raw)
    blocked = {"cookie", "host", "content-length", "content-type", "accept"}
    return {key: item for key, item in value.items() if key.casefold() not in blocked}


class AlexaRoomSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Alexa Room Sync setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Configure and validate a captured Alexa session."""
        errors: dict[str, str] = {}
        accounts = alexa_media_accounts(self.hass)
        if user_input is not None:
            try:
                if not _valid_host(user_input[CONF_HOST]):
                    errors[CONF_HOST] = "invalid_host"
                headers = _parse_headers(user_input[CONF_HEADERS_JSON])
                parse_string_mapping(user_input[CONF_MANUAL_MAPPINGS_JSON])
                parse_string_list(user_input[CONF_ENDPOINT_MODELS_JSON])
            except (json.JSONDecodeError, ValueError):
                errors["base"] = "invalid_json"
            if not errors:
                auth_method = user_input[CONF_AUTH_METHOD]
                if auth_method == AUTH_ALEXA_MEDIA:
                    resolved = get_alexa_media_login(
                        self.hass, user_input.get(CONF_ALEXA_MEDIA_ENTRY_ID)
                    )
                    if resolved is None:
                        errors["base"] = "alexa_media_unavailable"
                        api = None
                    else:
                        alexa_media_entry_id, login_obj = resolved
                        api = AlexaRoomApi.from_alexa_media(
                            login_obj, user_input[CONF_HOST], headers
                        )
                else:
                    cookie = user_input.get(CONF_COOKIE, "").strip()
                    if not cookie:
                        errors["base"] = "invalid_auth"
                        api = None
                    else:
                        api = AlexaRoomApi(
                            async_get_clientsession(self.hass),
                            user_input[CONF_HOST],
                            cookie,
                            headers,
                        )
                if api is None:
                    return self._show_form(user_input, accounts, errors)
                try:
                    await api.async_list_groups()
                except AlexaAuthError:
                    errors["base"] = "invalid_auth"
                except AlexaApiError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id("account")
                    self._abort_if_unique_id_configured()
                    data = dict(user_input)
                    data[CONF_HEADERS_JSON] = json.dumps(headers)
                    if auth_method == AUTH_ALEXA_MEDIA:
                        data[CONF_ALEXA_MEDIA_ENTRY_ID] = alexa_media_entry_id
                        data.pop(CONF_COOKIE, None)
                    return self.async_create_entry(
                        title="Alexa Room Sync", data=data
                    )

        return self._show_form(user_input, accounts, errors)

    def _show_form(
        self,
        user_input: dict | None,
        accounts: dict[str, str],
        errors: dict[str, str],
    ) -> FlowResult:
        """Show setup form with the authentication methods available now."""
        auth_options = {AUTH_COOKIE: "Cookie da HAR"}
        if accounts:
            auth_options = {
                AUTH_ALEXA_MEDIA: "Alexa Media Player (consigliato)",
                **auth_options,
            }
        default_auth = AUTH_ALEXA_MEDIA if accounts else AUTH_COOKIE
        fields: dict = {
            vol.Required(
                CONF_AUTH_METHOD,
                default=(user_input or {}).get(CONF_AUTH_METHOD, default_auth),
            ): vol.In(auth_options),
        }
        if accounts:
            fields[
                vol.Required(
                    CONF_ALEXA_MEDIA_ENTRY_ID,
                    default=(user_input or {}).get(
                        CONF_ALEXA_MEDIA_ENTRY_ID, next(iter(accounts))
                    ),
                )
            ] = vol.In(accounts)
        fields.update(
            {
                vol.Required(
                    CONF_HOST,
                    default=(user_input or {}).get(CONF_HOST, DEFAULT_HOST),
                ): str,
                vol.Optional(CONF_COOKIE): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD, multiline=True
                    )
                ),
                vol.Optional(
                    CONF_HEADERS_JSON,
                    default=(user_input or {}).get(CONF_HEADERS_JSON, "{}"),
                ): TextSelector(TextSelectorConfig(multiline=True)),
                vol.Required(
                    CONF_ENDPOINT_MODELS_JSON,
                    default=(user_input or {}).get(
                        CONF_ENDPOINT_MODELS_JSON, DEFAULT_ENDPOINT_MODELS_JSON
                    ),
                ): TextSelector(TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_MANUAL_MAPPINGS_JSON,
                    default=(user_input or {}).get(
                        CONF_MANUAL_MAPPINGS_JSON, "{}"
                    ),
                ): TextSelector(TextSelectorConfig(multiline=True)),
            }
        )
        schema = vol.Schema(
            fields
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
