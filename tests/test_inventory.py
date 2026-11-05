"""Alias collection and floor/domain filtering with representative HA registries."""

import sys
from types import SimpleNamespace as NS

import pytest

from custom_components.alexa_room_sync import inventory, runtime
from custom_components.alexa_room_sync.api import AlexaApiError
from custom_components.alexa_room_sync.models import AlexaEndpoint
from custom_components.alexa_room_sync.planner import (
    HomeAssistantCandidate as Candidate,
)


@pytest.fixture
def registries(monkeypatch):
    areas = [
        NS(id="a", name="Kitchen", floor_id="f"),
        NS(id="b", name="Office", floor_id="f"),
    ]
    monkeypatch.setattr(
        inventory.ar, "async_get", lambda hass: NS(async_list_areas=lambda: areas)
    )
    monkeypatch.setattr(
        inventory.dr,
        "async_get",
        lambda hass: NS(devices={}, async_get=lambda key: None),
    )
    monkeypatch.setattr(inventory.er, "async_get", lambda hass: NS(entities={}))
    floor = NS(async_get=lambda hass: NS(async_get_floor=lambda key: NS(name="Ground")))
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.floor_registry", floor)
    monkeypatch.setattr(
        sys.modules["homeassistant.helpers"], "floor_registry", floor, raising=False
    )
    return areas


def test_floor_can_overlap_area_and_excludes_echos(registries):
    candidates = [
        Candidate("light.k", "Lamp", "Kitchen", "a"),
        Candidate("media_player.echo", "Echo", "Kitchen", "a"),
    ]
    mapped = [
        {"endpoint_id": "lamp", "group_name": "Kitchen", "entity_ids": ["light.k"]},
        {
            "endpoint_id": "echo",
            "group_name": "Kitchen",
            "entity_ids": ["media_player.echo"],
            "echo": True,
        },
    ]
    rooms, _ = inventory.build_rooms(
        None,
        candidates,
        mapped,
        {"floor_groups": True, "floor_lights_only": False},
        set(),
        None,
    )
    assert rooms["area:a"]["members"] == {"lamp", "echo"}
    assert rooms["floor:f"]["members"] == {"lamp"}


def test_floor_lights_filter_excludes_thermostats(registries):
    candidates = [
        Candidate("light.k", "Lamp", "Kitchen", "a"),
        Candidate("climate.k", "Heat", "Kitchen", "a"),
    ]
    mapped = [
        {"endpoint_id": "lamp", "group_name": "Kitchen", "entity_ids": ["light.k"]},
        {"endpoint_id": "heat", "group_name": "Kitchen", "entity_ids": ["climate.k"]},
    ]
    rooms, _ = inventory.build_rooms(
        None, candidates, mapped, {"floor_groups": True}, set(), None
    )
    assert rooms["floor:f"]["members"] == {"lamp"}
    assert rooms["area:a"]["members"] == {"lamp", "heat"}


def test_skips_excluded_area_but_explicit_scope_can_target_it(registries):
    candidates = [Candidate("light.k", "Lamp", "Kitchen", "a")]
    mapped = [
        {"endpoint_id": "lamp", "group_name": "Kitchen", "entity_ids": ["light.k"]}
    ]
    opts = {"excluded_areas": ["a"], "group_prefix": "HA ", "floor_groups": True}
    assert inventory.build_rooms(None, candidates, mapped, opts, set(), None)[0] == {}
    rooms, _ = inventory.build_rooms(None, candidates, mapped, opts, set(), "a")
    assert list(rooms) == ["area:a"]
    assert rooms["area:a"]["name"] == "HA Kitchen"


def test_duplicate_area_names_fail_closed(registries):
    candidates = [
        Candidate("light.k", "Lamp", "Kitchen", "a"),
        Candidate("light.x", "X", "Kitchen", "b"),
    ]
    with pytest.raises(AlexaApiError):
        inventory.build_rooms(
            None,
            candidates,
            [{"endpoint_id": "lamp", "group_name": "Kitchen"}],
            {},
            set(),
            None,
        )


def test_missing_cloud_report_tracks_matched_entity_identity(registries):
    candidates = [
        Candidate("light.k", "Lamp", "Kitchen", "a"),
        Candidate("light.lost", "Lost", "Kitchen", "a"),
    ]
    _, missing = inventory.build_rooms(
        None,
        candidates,
        [{"endpoint_id": "lamp", "group_name": "Kitchen", "entity_ids": ["light.k"]}],
        {},
        {"light.k", "light.lost"},
        None,
    )
    assert missing == {"light.lost": "Lost"}


def test_ha_aliases_are_collected(registries, monkeypatch):
    entity = NS(
        entity_id="light.k",
        name="Lamp",
        original_name="Lamp",
        device_id=None,
        area_id="a",
        disabled_by=None,
        aliases={"Kitchen light", "Lampada"},
    )
    monkeypatch.setattr(
        runtime.er, "async_get", lambda hass: NS(entities={"light.k": entity})
    )
    hass = NS(states=NS(get=lambda key: None))
    names = {c.name for c in runtime.collect_candidates(hass)}
    assert names == {"Lamp", "Kitchen light", "Lampada"}


def test_echo_serial_wins_over_a_renamed_device(registries, monkeypatch):
    entity = NS(
        entity_id="media_player.echo",
        device_id="d",
        area_id="a",
        disabled_by=None,
        config_entry_id="account",
    )
    device = NS(
        id="d",
        model="Echo Dot",
        config_entries={"account"},
        area_id="a",
        identifiers={("alexa_media", "serial_user_example_com")},
    )
    monkeypatch.setattr(
        inventory.er, "async_get", lambda hass: NS(entities={entity.entity_id: entity})
    )
    monkeypatch.setattr(
        inventory.dr, "async_get", lambda hass: NS(devices={"d": device})
    )
    hass = NS(config_entries=NS(async_entries=lambda domain: []))
    endpoints = [
        AlexaEndpoint(
            "echo",
            "A different name",
            category="ALEXA_VOICE_ENABLED",
            serials=("SERIAL",),
        )
    ]
    mappings, blocked, issues = inventory.echo_mappings(
        hass, endpoints, [], "account", None
    )
    assert mappings == {"media_player.echo": "echo"}
    assert not blocked and not issues


def test_duplicate_echo_serial_blocks_both_endpoints(registries, monkeypatch):
    entity = NS(
        entity_id="media_player.echo",
        device_id="d",
        area_id="a",
        disabled_by=None,
        config_entry_id="account",
    )
    device = NS(
        id="d",
        model="Echo",
        config_entries={"account"},
        area_id="a",
        identifiers={("alexa_media", "SERIAL")},
    )
    monkeypatch.setattr(
        inventory.er, "async_get", lambda hass: NS(entities={entity.entity_id: entity})
    )
    monkeypatch.setattr(
        inventory.dr, "async_get", lambda hass: NS(devices={"d": device})
    )
    hass = NS(config_entries=NS(async_entries=lambda domain: []))
    endpoints = [
        AlexaEndpoint(i, i, category="ALEXA_VOICE_ENABLED", serials=("SERIAL",))
        for i in ("one", "two")
    ]
    mappings, blocked, issues = inventory.echo_mappings(
        hass, endpoints, [], "account", None
    )
    assert not mappings and blocked == {"one", "two"}
    assert issues[0]["reason"] == "ambiguous_echo_serial"


def test_echoes_from_another_account_are_not_serial_matched(registries, monkeypatch):
    device = NS(
        id="d",
        model="Echo",
        config_entries={"other-account"},
        area_id="a",
        identifiers={("alexa_media", "SERIAL")},
    )
    monkeypatch.setattr(
        inventory.dr, "async_get", lambda hass: NS(devices={"d": device})
    )
    hass = NS(config_entries=NS(async_entries=lambda domain: []))
    endpoints = [
        AlexaEndpoint(
            "echo", "Echo", category="ALEXA_VOICE_ENABLED", serials=("SERIAL",)
        )
    ]
    assert inventory.echo_mappings(hass, endpoints, [], "my-account", None) == (
        {},
        set(),
        [],
    )


def test_excluded_untracked_group_is_not_touched_by_legacy_migration(
    registries, monkeypatch
):
    from custom_components.alexa_room_sync.models import AlexaGroup

    candidates = [
        Candidate("light.desk", "Desk", "Office", "b"),
        Candidate("light.k", "Kitchen lamp", "Kitchen", "a"),
    ]
    monkeypatch.setattr(runtime, "collect_candidates", lambda hass: candidates)
    service = runtime.AlexaRoomSyncRuntime(None, {}, frozenset({"MatterHub"}))
    service.options.update(include_echos=False, excluded_areas=["a"])
    endpoint = AlexaEndpoint(
        "desk", "Desk", model="MatterHub", source_entity_id="light.desk"
    )
    groups = [
        AlexaGroup("k", "Kitchen", frozenset({"desk"})),
        AlexaGroup("o", "Office", frozenset()),
    ]
    plan = service._plan(None, [endpoint], groups)
    assert all(a.get("group_id") != "k" for a in plan.group_actions)
    assert not plan.removals
    assert plan.additions[0].group_id == "o"


def test_duplicate_legacy_source_groups_are_not_modified(registries, monkeypatch):
    from custom_components.alexa_room_sync.models import AlexaGroup

    candidates = [
        Candidate("light.desk", "Desk", "Office", "b"),
        Candidate("light.k", "Kitchen lamp", "Kitchen", "a"),
    ]
    monkeypatch.setattr(runtime, "collect_candidates", lambda hass: candidates)
    service = runtime.AlexaRoomSyncRuntime(None, {}, frozenset({"MatterHub"}))
    service.options["include_echos"] = False
    endpoint = AlexaEndpoint(
        "desk", "Desk", model="MatterHub", source_entity_id="light.desk"
    )
    groups = [AlexaGroup(i, "Kitchen", frozenset({"desk"})) for i in ("k1", "k2")]
    groups.append(AlexaGroup("o", "Office", frozenset()))
    plan = service._plan(None, [endpoint], groups)
    assert not plan.removals
    assert any(a["reason"] == "duplicate_alexa_groups" for a in plan.ambiguous)
