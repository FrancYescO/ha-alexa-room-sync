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
from .auth import get_alexa_devices_api, get_alexa_media_login
from .const import (
    AUTH_ALEXA_DEVICES,
    AUTH_ALEXA_MEDIA,
    CONF_ALEXA_DEVICES_ENTRY_ID,
    CONF_ALEXA_MEDIA_ENTRY_ID,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_ENDPOINT_MODELS_JSON,
    CONF_HEADERS_JSON,
    CONF_HOST,
    CONF_MANUAL_MAPPINGS_JSON,
    DEFAULT_OPTIONS,
    DOMAIN,
    EVENT_SYNC_FINISHED,
    PLATFORMS,
    SERVICE_APPLY,
    SERVICE_CAPABILITIES,
    SERVICE_CLEANUP_PREVIEW,
    SERVICE_DELETE_STALE_ENDPOINTS,
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
    alexa_media = get_alexa_media_login(hass, entry.data.get(CONF_ALEXA_MEDIA_ENTRY_ID))

    api: AlexaRoomApi
    if auth_method == AUTH_ALEXA_DEVICES:
        try:
            get_alexa_devices_api(hass, entry.data[CONF_ALEXA_DEVICES_ENTRY_ID])
        except RuntimeError as err:
            raise ConfigEntryNotReady(str(err)) from err
        api = AlexaRoomApi.from_alexa_devices(
            lambda: get_alexa_devices_api(
                hass, entry.data[CONF_ALEXA_DEVICES_ENTRY_ID]
            ),
            entry.data[CONF_HOST],
            extra_headers,
        )
    elif auth_method == AUTH_ALEXA_MEDIA:
        if alexa_media is None:
            raise ConfigEntryNotReady(
                "Alexa Media Player non è caricato o richiede una nuova autenticazione"
            )
        api = AlexaRoomApi.from_alexa_media(
            lambda: _current_media_login(hass, entry.data[CONF_ALEXA_MEDIA_ENTRY_ID]),
            entry.data[CONF_HOST],
            extra_headers,
        )
    elif auth_method is None and alexa_media is not None:
        # Migrate legacy HAR/cookie entries only after proving that the Alexa
        # Media Player session can access the required GraphQL endpoint.
        alexa_media_entry_id, _ = alexa_media
        candidate_api = AlexaRoomApi.from_alexa_media(
            lambda: _current_media_login(hass, alexa_media_entry_id),
            entry.data[CONF_HOST],
            extra_headers,
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
            auth_method = AUTH_ALEXA_MEDIA
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
        options={**DEFAULT_OPTIONS, **entry.options},
        entry_id=entry.entry_id,
        auth_entry_id=entry.data.get(CONF_ALEXA_DEVICES_ENTRY_ID)
        if auth_method == AUTH_ALEXA_DEVICES
        else entry.data.get(CONF_ALEXA_MEDIA_ENTRY_ID)
        if auth_method == AUTH_ALEXA_MEDIA
        else None,
    )

    from homeassistant.helpers.storage import Store

    runtime = entry.runtime_data
    runtime.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.groups")
    saved = await runtime.store.async_load() or {}
    runtime.records = saved.get("records", {})
    runtime.selected_area = saved.get("selected_area")
    runtime.last_result = saved.get("last_result", {})
    source = (
        hass.config_entries.async_get_entry(runtime.auth_entry_id)
        if runtime.auth_entry_id
        else None
    )
    if source:
        runtime.account_email = source.data.get("email") or source.data.get("username")
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    if runtime.options.get("auto_sync"):
        from .autosync import async_start

        entry.async_on_unload(async_start(hass, entry))

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
            return (
                await entry.runtime_data.async_plan(hass, call.data.get("area_id"))
            ).as_dict()
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    async def _apply(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        try:
            result = await entry.runtime_data.async_apply(
                hass, call.data.get("area_id")
            )
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        hass.bus.async_fire(EVENT_SYNC_FINISHED, result)
        return result

    async def _capabilities(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        try:
            capabilities = await entry.runtime_data.api.async_group_capabilities()
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        return capabilities

    async def _cleanup_preview(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        try:
            stale = await entry.runtime_data.async_prepare_stale_cleanup(hass)
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        return {"stale_count": len(stale), "stale_endpoints": stale}

    async def _delete_stale_endpoints(call: ServiceCall) -> dict[str, Any]:
        await _assert_admin(call)
        if call.data["confirm"] is not True:
            raise HomeAssistantError(
                "Imposta confirm: true per autorizzare la cancellazione"
            )
        try:
            result = await entry.runtime_data.async_delete_stale_endpoints(
                hass, call.data["endpoint_ids"]
            )
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        return result

    async def _remove_groups(call: ServiceCall):
        await _assert_admin(call)
        if call.data["confirm"] is not True:
            raise HomeAssistantError("Imposta confirm: true")
        try:
            return await runtime.async_apply(hass, teardown=True)
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    async def _forget_missing_groups(call: ServiceCall):
        await _assert_admin(call)
        if call.data["confirm"] is not True:
            raise HomeAssistantError("Imposta confirm: true")
        try:
            return await runtime.async_forget_missing_groups(call.data["group_keys"])
        except (AlexaApiError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        "forget_missing_groups",
        _forget_missing_groups,
        schema=vol.Schema(
            {
                vol.Required("confirm"): bool,
                vol.Required("group_keys"): vol.All([str], vol.Length(min=1, max=100)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    area_schema = vol.Schema({vol.Optional("area_id"): str})
    hass.services.async_register(
        DOMAIN,
        "sync_area",
        _apply,
        schema=vol.Schema({vol.Required("area_id"): str}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "remove_managed_groups",
        _remove_groups,
        schema=vol.Schema({vol.Required("confirm"): bool}),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CAPABILITIES,
        _capabilities,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_PREVIEW,
        _cleanup_preview,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_STALE_ENDPOINTS,
        _delete_stale_endpoints,
        schema=vol.Schema(
            {
                vol.Required("endpoint_ids"): vol.All(
                    [str], vol.Length(min=1, max=100)
                ),
                vol.Required("confirm"): bool,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PREVIEW,
        _preview,
        schema=area_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY,
        _apply,
        schema=area_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AlexaRoomSyncConfigEntry
) -> bool:
    """Unload Alexa Room Sync."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    hass.services.async_remove(DOMAIN, "forget_missing_groups")
    hass.services.async_remove(DOMAIN, "sync_area")
    hass.services.async_remove(DOMAIN, "remove_managed_groups")
    hass.services.async_remove(DOMAIN, SERVICE_PREVIEW)
    hass.services.async_remove(DOMAIN, SERVICE_APPLY)
    hass.services.async_remove(DOMAIN, SERVICE_CAPABILITIES)
    hass.services.async_remove(DOMAIN, SERVICE_CLEANUP_PREVIEW)
    hass.services.async_remove(DOMAIN, SERVICE_DELETE_STALE_ENDPOINTS)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget local ownership only. Uninstalling never changes Alexa."""
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.groups").async_remove()


def _current_media_login(hass, entry_id):
    from .api import AlexaAuthError

    resolved = get_alexa_media_login(hass, entry_id)
    if resolved is None:
        raise AlexaAuthError("Alexa Media Player richiede autenticazione")
    return resolved[1]
