"""Persistent area selection that follows registry renames."""

from collections import Counter

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AlexaRoomSyncAreaSelect(entry)])


class AlexaRoomSyncAreaSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "area"
    _attr_icon = "mdi:floor-plan"
    _attr_should_poll = False

    def __init__(self, entry):
        self._entry = entry
        self._areas = {}
        self._attr_unique_id = f"{entry.entry_id}_area"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self):
        self._refresh()
        self.async_on_remove(
            self.hass.bus.async_listen("area_registry_updated", self._changed)
        )

    @callback
    def _changed(self, _event):
        self._refresh()
        self.async_write_ha_state()

    def _refresh(self):
        excluded = set(self._entry.runtime_data.options.get("excluded_areas", []))
        areas = [
            a
            for a in ar.async_get(self.hass).async_list_areas()
            if a.id not in excluded
        ]
        counts = Counter(a.name for a in areas)
        self._areas = {
            a.name if counts[a.name] == 1 else f"{a.name} ({a.id})": a.id for a in areas
        }
        self._attr_options = sorted(self._areas)

    @property
    def current_option(self):
        return next(
            (
                label
                for label, area in self._areas.items()
                if area == self._entry.runtime_data.selected_area
            ),
            None,
        )

    async def async_select_option(self, option):
        self._refresh()
        if option not in self._areas:
            raise ValueError("Unknown area")
        runtime = self._entry.runtime_data
        async with runtime.lock:
            runtime.selected_area = self._areas[option]
            await runtime.async_save()
        self.async_write_ha_state()
