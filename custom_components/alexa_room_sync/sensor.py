"""Last synchronization outcome, persisted across restarts."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AlexaRoomSyncResultSensor(entry)])


class AlexaRoomSyncResultSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "last_result"
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"result"})

    def __init__(self, entry):
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_last_result"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self):
        self._entry.runtime_data.result_entity_id = self.entity_id
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self._entry.entry_id}_result",
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self):
        result = self._entry.runtime_data.last_result
        if not result:
            return None
        return f"{result.get('mode', 'unknown')}: {result.get('applied_change_count', result.get('change_count', 0))}"

    @property
    def extra_state_attributes(self):
        result = self._entry.runtime_data.last_result
        return {
            "ran_at": result.get("ran_at"),
            "scope": result.get("scope"),
            "mode": result.get("mode"),
            "result": result,
        }
