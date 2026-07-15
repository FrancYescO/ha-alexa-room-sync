"""Alexa Room Sync integration."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    Unauthorized,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AlexaApiError, AlexaRoomApi
from .auth import get_alexa_media_login
from .const import (
    AUTH_ALEXA_MEDIA,
    CONF_ALEXA_MEDIA_ENTRY_ID,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_ENDPOINT_MODELS_JSON,
    CONF_HEADERS_JSON,
    CONF_HOST,
    CONF_MANUAL_MAPPINGS_JSON,
    DOMAIN,
    EVENT_SYNC_FINISHED,
    SERVICE_APPLY,
    SERVICE_CAPABILITIES,
    SERVICE_PREVIEW,
)
from .runtime import AlexaRoomSyncRuntime, parse_string_list, parse_string_mapping

_LOGGER = logging.getLogger(__name__)

AlexaRoomSyncConfigEntry = ConfigEntry[AlexaRoomSyncRuntime]


async def async_setup_entry(
    hass: HomeAssistant, entry: AlexaRoomSyncConfigEntry
) -> bool:
    """Set up Alexa Room Sync from a config entry."""
    extra_headers = json.loads(entry.data.get(CONF_HEADERS_JSON, "{}"))
    auth_method = entry.data.get(CONF_AUTH_METHOD)
    alexa_media = get_alexa_media_login(
        hass, entry.data.get(CONF_ALEXA_MEDIA_ENTRY_ID)
    )

    api: AlexaRoomApi
    if auth_method == AUTH_ALEXA_MEDIA:
        if alexa_media is None:
            raise ConfigEntryNotReady(
                "Alexa Media Player non è caricato o richiede una nuova autenticazione"
            )
        _, login_obj = alexa_media
        api = AlexaRoomApi.from_alexa_media(
            login_obj, entry.data[CONF_HOST], extra_headers
        )
    elif auth_method is None and alexa_media is not None:
        # Migrate legacy HAR/cookie entries only after proving that the Alexa
        # Media Player session can access the required GraphQL endpoint.
        alexa_media_entry_id, login_obj = alexa_media
        candidate_api = AlexaRoomApi.from_alexa_media(
            login_obj, entry.data[CONF_HOST], extra_headers
        )
        try:
            await candidate_api.async_list_groups()
        except AlexaApiError:
            api = AlexaRoomApi(
                async_get_clientsession(hass),
                entry.data[CONF_HOST],
                entry.data.get(CONF_COOKIE),
                extra_headers,
            )
        else:
            migrated_data = dict(entry.data)
            migrated_data.pop(CONF_COOKIE, None)
            migrated_data[CONF_AUTH_METHOD] = AUTH_ALEXA_MEDIA
            migrated_data[CONF_ALEXA_MEDIA_ENTRY_ID] = alexa_media_entry_id
            hass.config_entries.async_update_entry(entry, data=migrated_data)
            api = candidate_api
            _LOGGER.info(
                "Migrated authentication to Alexa Media Player config entry %s",
                alexa_media_entry_id,
            )
    else:
        api = AlexaRoomApi(
            async_get_clientsession(hass),
            entry.data[CONF_HOST],
            entry.data.get(CONF_COOKIE),
            extra_headers,
        )

    entry.runtime_data = AlexaRoomSyncRuntime(
        api=api,
        manual_mappings=parse_string_mapping(
            entry.data.get(CONF_MANUAL_MAPPINGS_JSON, "{}")
        ),
        endpoint_models=parse_string_list(entry.data[CONF_ENDPOINT_MODELS_JSON]),
    )

    async def _assert_admin(call: ServiceCall) -> None:
        if call.context.user_id is None:
            return
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None or not user.is_admin:
            raise Unauthorized(
                context=call.context,
                user_id=call.context.user_id,
                config_entry_id=entry.entry_id,
            )

    async def _preview(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        try:
            return (await entry.runtime_data.async_plan(hass)).as_dict()
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def _apply(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        try:
            result = await entry.runtime_data.async_apply(hass)
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err
        hass.bus.async_fire(EVENT_SYNC_FINISHED, result)
        return result

    async def _capabilities(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        try:
            capabilities = await entry.runtime_data.api.async_group_capabilities()
        except AlexaApiError as err:
            raise HomeAssistantError(str(err)) from err
        return capabilities

    hass.services.async_register(
        DOMAIN,
        SERVICE_CAPABILITIES,
        _capabilities,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PREVIEW,
        _preview,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY,
        _apply,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AlexaRoomSyncConfigEntry
) -> bool:
    """Unload Alexa Room Sync."""
    hass.services.async_remove(DOMAIN, SERVICE_PREVIEW)
    hass.services.async_remove(DOMAIN, SERVICE_APPLY)
    hass.services.async_remove(DOMAIN, SERVICE_CAPABILITIES)
    return True
