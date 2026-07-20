"""Button entities for Alexa Room Sync."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.persistent_notification import async_create
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AlexaRoomSyncConfigEntry
from .api import AlexaApiError
from .const import DOMAIN, EVENT_SYNC_FINISHED
from .notifications import (
    format_cleanup_delete_notification,
    format_cleanup_preview_notification,
    format_preview_notification,
    format_sync_notification,
)

_LOGGER = logging.getLogger(__name__)

BUTTONS = (
    ButtonEntityDescription(
        key="verify",
        translation_key="verify",
        icon="mdi:clipboard-search-outline",
    ),
    ButtonEntityDescription(
        key="sync",
        translation_key="sync",
        icon="mdi:sync",
    ),
    ButtonEntityDescription(
        key="cleanup",
        translation_key="cleanup",
        icon="mdi:broom",
    ),
    ButtonEntityDescription(
        key="delete_stale",
        translation_key="delete_stale",
        icon="mdi:delete-alert-outline",
    ),
)


async def async_setup_entry(
    hass, entry: AlexaRoomSyncConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up Alexa Room Sync buttons."""
    async_add_entities(AlexaRoomSyncButton(entry, description) for description in BUTTONS)


class AlexaRoomSyncButton(ButtonEntity):
    """Run verification or synchronization on demand."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: AlexaRoomSyncConfigEntry,
        description: ButtonEntityDescription,
    ) -> None:
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Alexa Room Sync",
            manufacturer="Alexa Room Sync",
            model="Room synchronization service",
        )

    async def async_press(self) -> None:
        """Run the selected action and publish its result."""
        try:
            if self.entity_description.key == "verify":
                result = (await self._entry.runtime_data.async_plan(self.hass)).as_dict()
                message = format_preview_notification(result)
                title = "Alexa Room Sync · Verifica"
            elif self.entity_description.key == "sync":
                result = await self._entry.runtime_data.async_apply(self.hass)
                self.hass.bus.async_fire(EVENT_SYNC_FINISHED, result)
                message = format_sync_notification(result)
                title = "Alexa Room Sync · Sincronizzazione"
            elif self.entity_description.key == "cleanup":
                stale = await self._entry.runtime_data.async_prepare_stale_cleanup(
                    self.hass
                )
                message = format_cleanup_preview_notification(stale)
                title = "Alexa Room Sync · Endpoint obsoleti"
            else:
                result = (
                    await self._entry.runtime_data.async_delete_prepared_stale_endpoints(
                        self.hass
                    )
                )
                message = format_cleanup_delete_notification(result)
                title = "Alexa Room Sync · Pulizia completata"
        except ValueError as err:
            _LOGGER.warning("Alexa Room Sync button action blocked: %s", err)
            async_create(
                self.hass,
                f"Operazione bloccata: {err}",
                title="Alexa Room Sync · Protezione pulizia",
                notification_id=f"{DOMAIN}_{self.entity_description.key}",
            )
            return
        except AlexaApiError as err:
            _LOGGER.error("Alexa Room Sync button action failed: %s", err)
            async_create(
                self.hass,
                f"Operazione non riuscita: {err}",
                title="Alexa Room Sync · Errore",
                notification_id=f"{DOMAIN}_{self.entity_description.key}",
            )
            raise HomeAssistantError(str(err)) from err

        async_create(
            self.hass,
            message,
            title=title,
            notification_id=f"{DOMAIN}_{self.entity_description.key}",
        )
