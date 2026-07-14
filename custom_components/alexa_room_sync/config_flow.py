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
from .const import (
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
                api = AlexaRoomApi(
                    async_get_clientsession(self.hass),
                    user_input[CONF_HOST],
                    user_input[CONF_COOKIE],
                    headers,
                )
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
                    return self.async_create_entry(
                        title="Alexa Room Sync", data=data
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=(user_input or {}).get(CONF_HOST, DEFAULT_HOST),
                ): str,
                vol.Required(CONF_COOKIE): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=True)
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
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
