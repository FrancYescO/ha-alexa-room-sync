"""Run with real Home Assistant, independently of the lightweight pytest stubs.

    python tests/ha_smoke.py

No network calls or real Amazon credentials are used.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.storage import Store

from custom_components.alexa_room_sync.config_flow import (
    AlexaRoomSyncConfigFlow,
    AlexaRoomSyncOptionsFlow,
)
from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup
from custom_components.alexa_room_sync.runtime import AlexaRoomSyncRuntime
from custom_components.alexa_room_sync.select import AlexaRoomSyncAreaSelect
from custom_components.alexa_room_sync.sensor import AlexaRoomSyncResultSensor


class SimulatedAlexa:
    def __init__(self, entity_id):
        self.groups = {
            "unmanaged": AlexaGroup("unmanaged", "Music", frozenset({"manual"}))
        }
        self.endpoints = [
            AlexaEndpoint("lamp", "Spoken alias", model="MatterHub"),
            AlexaEndpoint(
                "desk", "Desk", model="MatterHub", source_entity_id=entity_id
            ),
        ]
        self.writes = []

    async def async_list_endpoints(self):
        return self.endpoints

    async def async_list_groups(self):
        return list(self.groups.values())

    async def async_create_group(self, name):
        group_id = f"group-{len(self.groups)}"
        self.groups[group_id] = AlexaGroup(group_id, name, frozenset())
        self.writes.append(("CREATE", group_id))

    async def async_update_group(self, group_id, endpoint_id, operation):
        group = self.groups[group_id]
        members = set(group.member_ids)
        members.add(endpoint_id) if operation == "ADD" else members.discard(endpoint_id)
        self.groups[group_id] = AlexaGroup(group_id, group.name, frozenset(members))
        self.writes.append((operation, group_id))

    async def async_rename_group(self, group, name, endpoints):
        self.groups[group.group_id] = AlexaGroup(group.group_id, name, group.member_ids)
        self.writes.append(("RENAME", group.group_id))

    async def async_delete_group(self, group, endpoints):
        del self.groups[group.group_id]
        self.writes.append(("DELETE", group.group_id))


async def main():
    with TemporaryDirectory() as directory:
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        if hasattr(dr, "async_setup"):
            dr.async_setup(hass)
        for registry in (fr, ar, dr, er):
            await registry.async_load(hass, load_empty=True)
        floors, areas, entities = (
            fr.async_get(hass),
            ar.async_get(hass),
            er.async_get(hass),
        )
        floor = floors.async_create("Ground")
        kitchen = areas.async_create("Kitchen", floor_id=floor.floor_id)
        office = areas.async_create("Office", floor_id=floor.floor_id)
        lamp = entities.async_get_or_create(
            "light", "test", "lamp", suggested_object_id="lamp"
        )
        desk = entities.async_get_or_create(
            "light", "test", "desk", suggested_object_id="desk"
        )
        entities.async_update_entity(
            lamp.entity_id, area_id=kitchen.id, aliases={"Spoken alias"}
        )
        entities.async_update_entity(desk.entity_id, area_id=office.id)
        hass.states.async_set(lamp.entity_id, "off", {"friendly_name": "Lamp"})
        hass.states.async_set(desk.entity_id, "off", {"friendly_name": "Desk"})
        api = SimulatedAlexa(desk.entity_id)
        runtime = AlexaRoomSyncRuntime(
            api,
            {},
            frozenset({"MatterHub"}),
            entry_id="test",
            store=Store(hass, 1, "alexa_room_sync.smoke"),
        )
        runtime.options.update(include_echos=False, floor_groups=True)
        plan = await runtime.async_plan(hass)
        assert set(plan.as_dict()["groups_to_create"]) == {
            "Kitchen",
            "Office",
            "Ground",
        }
        assert not api.writes
        assert (await runtime.async_apply(hass))["mode"] == "preview"
        assert not api.writes
        runtime.options.update(dry_run=False, allow_writes=True)
        result = await runtime.async_apply(hass)
        assert len(result["created_groups"]) == 3
        assert len(runtime.records) == 3
        assert not (await runtime.async_plan(hass)).as_dict()["change_count"]
        assert (await runtime.store.async_load())["records"] == runtime.records
        kitchen_group = runtime.records[f"area:{kitchen.id}"]["group_id"]
        areas.async_update(kitchen.id, name="Dining")
        result = await runtime.async_apply(hass)
        assert api.groups[kitchen_group].name == "Dining"
        assert any(o["operation"] == "RENAME" for o in result["completed"])
        group = api.groups[kitchen_group]
        api.groups[kitchen_group] = AlexaGroup(
            group.group_id, group.name, group.member_ids | {"manual-echo"}
        )
        entities.async_update_entity(lamp.entity_id, area_id=office.id)
        api.writes.clear()
        await runtime.async_apply(hass, office.id)
        office_group = runtime.records[f"area:{office.id}"]["group_id"]
        assert api.writes and all(
            group_id == office_group for _, group_id in api.writes
        )
        assert api.groups[kitchen_group].member_ids == {"lamp", "manual-echo"}
        await runtime.async_apply(hass)
        # The now-empty managed area is held first, then released (manual Echo survives).
        runtime.records[f"area:{kitchen.id}"]["retire_since"] = time.time() - 901
        await runtime.async_apply(hass)
        assert api.groups[kitchen_group].member_ids == {"manual-echo"}
        assert f"area:{kitchen.id}" not in runtime.records
        runtime.options["floor_groups"] = False
        await runtime.async_apply(hass)
        assert f"floor:{floor.floor_id}" in runtime.records
        runtime.records[f"floor:{floor.floor_id}"]["retire_since"] = time.time() - 901
        await runtime.async_apply(hass)
        assert f"floor:{floor.floor_id}" not in runtime.records
        assert api.groups["unmanaged"].member_ids == {"manual"}
        entry = SimpleNamespace(entry_id="test", runtime_data=runtime)
        select = AlexaRoomSyncAreaSelect(entry)
        # EntityPlatform reads capabilities before async_added_to_hass runs.
        # Access options here to exercise that order (and its cached property).
        assert select.capability_attributes == {"options": []}
        select.hass = hass
        select.async_write_ha_state = lambda: None
        select._refresh()
        assert "Office" in select.options
        await select.async_select_option("Office")
        assert runtime.selected_area == office.id
        sensor = AlexaRoomSyncResultSensor(entry)
        assert sensor.native_value.startswith("apply:")
        assert sensor.extra_state_attributes["mode"] == "apply"
        flow = AlexaRoomSyncConfigFlow()
        flow.hass = hass
        form = flow._show_form(None, {}, {})
        assert form["type"] == "form"
        assert "host" in [str(k) for k in form["data_schema"].schema]

        class OptionsTest(AlexaRoomSyncOptionsFlow):
            @property
            def config_entry(self):
                return SimpleNamespace(options={})

        options = OptionsTest()
        options.hass = hass
        option_form = await options.async_step_init()
        assert option_form["type"] == "form"
        invalid = await options.async_step_init(
            {"resend_missing": True, "cloud_devices": False}
        )
        assert invalid["errors"] == {"base": "cloud_required"}
        await hass.async_block_till_done()
        await hass.async_stop()
        print(
            "Real Home Assistant smoke test passed: aliases, preview gates, area/floor sync, rename, retirement, manual members, storage, selector and sensor"
        )


if __name__ == "__main__":
    asyncio.run(main())
