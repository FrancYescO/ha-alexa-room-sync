"""Run with real Home Assistant, independently of the lightweight pytest stubs.

    python tests/ha_smoke.py

No network calls or real Amazon credentials are used.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from homeassistant import loader
import voluptuous as vol
from homeassistant.config_entries import ConfigEntries, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.storage import Store

from custom_components import alexa_room_sync as integration
from custom_components.alexa_room_sync import config_flow
from custom_components.alexa_room_sync.config_flow import (
    AlexaRoomSyncConfigFlow,
    AlexaRoomSyncOptionsFlow,
)
from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup
from custom_components.alexa_room_sync.runtime import AlexaRoomSyncRuntime
from custom_components.alexa_room_sync.select import AlexaRoomSyncAreaSelect
from custom_components.alexa_room_sync.sensor import AlexaRoomSyncResultSensor


async def check_auth_startup(hass):
    """Exercise entry startup with an unavailable alternative provider."""
    manifest = json.loads(
        (Path(integration.__file__).parent / "manifest.json").read_text()
    )
    assert not {"alexa_devices", "alexa_media"}.intersection(
        manifest.get("after_dependencies", []) + manifest.get("dependencies", [])
    )

    for method, provider_available, session_available in (
        ("alexa_devices", True, True),
        ("alexa_media", True, True),
        ("cookie", True, True),
        ("alexa_devices", False, False),
        ("alexa_media", False, False),
        ("alexa_devices", True, False),
        ("alexa_media", True, False),
    ):
        sources = {
            domain: SimpleNamespace(
                entry_id=domain,
                title=domain,
                data={"email": "test@example.invalid"},
                state=ConfigEntryState.NOT_LOADED,
                runtime_data=None,
            )
            for domain in ("alexa_devices", "alexa_media")
        }

        async def setup_provider(instance, domain, config):
            assert instance is hass
            assert domain == method  # Never start an alternative provider.
            if provider_available and session_available:
                sources[domain].state = ConfigEntryState.LOADED
                sources[domain].runtime_data = SimpleNamespace(
                    api=object(), login_obj=object()
                )
            return provider_available

        entry = SimpleNamespace(
            entry_id="startup-test",
            data={
                "auth_method": method,
                "alexa_devices_entry_id": "alexa_devices",
                "alexa_media_entry_id": "alexa_media",
                "host": "https://eu-api-alexa.amazon.it",
                "endpoint_models_json": '["MatterHub"]',
                "cookie": "csrf=test",
            },
            options={},
            async_on_unload=Mock(),
            add_update_listener=Mock(return_value=lambda: None),
        )
        with (
            patch.object(
                integration, "async_setup_component", side_effect=setup_provider
            ) as setup,
            patch.object(integration, "async_get_clientsession", return_value=object()),
            patch.object(
                hass.config_entries,
                "async_entries",
                side_effect=lambda domain: [sources[domain]],
            ),
            patch.object(
                hass.config_entries, "async_get_entry", side_effect=sources.get
            ),
            patch.object(Store, "async_load", new=AsyncMock(return_value={})),
            patch.object(
                hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
            ) as forward,
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new=AsyncMock(return_value=True),
            ),
        ):
            if provider_available and session_available:
                assert await integration.async_setup_entry(hass, entry)
                forward.assert_awaited_once()
                expected_source = None if method == "cookie" else method
                assert entry.runtime_data.auth_entry_id == expected_source
                assert hass.services.has_service("alexa_room_sync", "preview")
                assert await integration.async_unload_entry(hass, entry)
            else:
                try:
                    await integration.async_setup_entry(hass, entry)
                except ConfigEntryNotReady:
                    pass
                else:
                    raise AssertionError("Unavailable provider must retry setup")
                forward.assert_not_awaited()
            if method == "cookie":
                setup.assert_not_called()
            else:
                setup.assert_awaited_once_with(hass, method, {})


async def check_optional_config_fields(hass):
    """Optional setup fields may be omitted, cleared, or populated on edit."""
    previous = {
        "auth_method": "cookie",
        "host": "https://eu-api-alexa.amazon.it",
        "endpoint_models_json": '["MatterHub"]',
        "cookie": "csrf=test",
        "headers_json": '{"X-Old": "stale"}',
        "manual_mappings_json": '{"light.old": "old-endpoint"}',
        "alexa_media_entry_id": "removed-account",
        "alexa_devices_entry_id": "removed-account",
    }
    for reconfigure in (False, True):
        for optional_input in (
            {},
            {"headers_json": "", "manual_mappings_json": ""},
            {
                "headers_json": '{"X-App": "custom"}',
                "manual_mappings_json": '{"light.kitchen": "endpoint"}',
            },
        ):
            flow = AlexaRoomSyncConfigFlow()
            flow.hass = hass
            flow.context = {"source": "reconfigure" if reconfigure else "user"}
            entry = SimpleNamespace(data=previous)
            with (
                patch.object(
                    config_flow, "alexa_media_accounts", return_value={"media": "Media"}
                ),
                patch.object(
                    config_flow,
                    "alexa_devices_accounts",
                    return_value={"devices": "Devices"},
                ),
                patch.object(
                    config_flow, "async_get_clientsession", return_value=object()
                ),
                patch.object(
                    config_flow.AlexaRoomApi,
                    "async_list_groups",
                    new=AsyncMock(return_value=[]),
                ),
                patch.object(flow, "async_set_unique_id", new=AsyncMock()),
                patch.object(flow, "_abort_if_unique_id_configured"),
                patch.object(flow, "_get_reconfigure_entry", return_value=entry),
                patch.object(
                    flow,
                    "async_update_reload_and_abort",
                    return_value={"type": "abort"},
                ) as update,
            ):
                form = flow._show_form(
                    previous if reconfigure else None, {"media": "Media"}, {}
                )
                schema = form["data_schema"]
                required = {
                    str(key) for key in schema.schema if isinstance(key, vol.Required)
                }
                assert required == {"auth_method", "host", "endpoint_models_json"}
                submitted = schema(
                    {"auth_method": "cookie", "cookie": "csrf=test", **optional_input}
                )
                assert submitted["alexa_media_entry_id"] == "media"
                assert submitted["alexa_devices_entry_id"] == "devices"
                result = await flow.async_step_user(submitted)
                data = (
                    update.call_args.kwargs["data"] if reconfigure else result["data"]
                )
                for key in ("headers_json", "manual_mappings_json"):
                    assert json.loads(data[key]) == json.loads(
                        optional_input.get(key) or "{}"
                    )


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
        await check_auth_startup(hass)
        await check_optional_config_fields(hass)
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
                return SimpleNamespace(options=getattr(self, "stored_options", {}))

        options = OptionsTest()
        options.hass = hass
        option_form = await options.async_step_init()
        assert option_form["type"] == "form"
        assert all(
            isinstance(key, vol.Optional) for key in option_form["data_schema"].schema
        )
        defaults = option_form["data_schema"]({})
        saved = await options.async_step_init(defaults)
        assert saved["data"]["group_prefix"] == ""
        assert saved["data"]["excluded_areas"] == []
        assert saved["data"]["dry_run"] is True
        assert saved["data"]["allow_writes"] is False
        options.stored_options = {
            "group_prefix": "HA ",
            "excluded_areas": [office.id],
            "dry_run": False,
            "allow_writes": True,
        }
        editing = await options.async_step_init()
        cleared = await options.async_step_init(editing["data_schema"]({}))
        assert cleared["data"]["group_prefix"] == ""
        assert cleared["data"]["excluded_areas"] == []
        assert cleared["data"]["dry_run"] is False
        assert cleared["data"]["allow_writes"] is True
        populated = await options.async_step_init(
            editing["data_schema"](
                {"group_prefix": "Alexa ", "excluded_areas": [office.id]}
            )
        )
        assert populated["data"]["group_prefix"] == "Alexa "
        assert populated["data"]["excluded_areas"] == [office.id]
        invalid = await options.async_step_init(
            {"resend_missing": True, "cloud_devices": False}
        )
        assert invalid["errors"] == {"base": "cloud_required"}
        await hass.async_block_till_done()
        await hass.async_stop()
        print(
            "Real Home Assistant smoke test passed: provider startup, optional forms, aliases, preview gates, area/floor sync, rename, retirement, manual members, storage, selector and sensor"
        )


if __name__ == "__main__":
    asyncio.run(main())
