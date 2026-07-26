"""Pure planner tests; run inside a Home Assistant development environment."""

from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup
from custom_components.alexa_room_sync.planner import (
    HomeAssistantCandidate,
    build_plan,
    build_room_audit,
)


def test_adds_endpoint_to_matching_area_group() -> None:
    plan = build_plan(
        [AlexaEndpoint("endpoint-1", "Luce tavolo")],
        [AlexaGroup("group-1", "Soggiorno", frozenset())],
        [HomeAssistantCandidate("light.tavolo", "Luce tavolo", "Soggiorno")],
    )
    assert len(plan.additions) == 1
    assert not plan.removals


def test_moves_only_between_managed_area_groups() -> None:
    plan = build_plan(
        [AlexaEndpoint("endpoint-1", "Luce tavolo")],
        [
            AlexaGroup("old", "Cucina", frozenset({"endpoint-1"})),
            AlexaGroup("new", "Soggiorno", frozenset()),
            AlexaGroup("other", "Tutte le luci", frozenset({"endpoint-1"})),
        ],
        [
            HomeAssistantCandidate("light.tavolo", "Luce tavolo", "Soggiorno"),
            HomeAssistantCandidate("light.cucina", "Luce cucina", "Cucina"),
        ],
    )
    assert [item.group_id for item in plan.removals] == ["old"]
    assert [item.group_id for item in plan.additions] == ["new"]


def test_duplicate_alexa_names_are_never_guessed() -> None:
    plan = build_plan(
        [
            AlexaEndpoint("one", "Lampada"),
            AlexaEndpoint("two", "Lampada"),
        ],
        [AlexaGroup("group", "Studio", frozenset())],
        [HomeAssistantCandidate("light.lampada", "Lampada", "Studio")],
    )
    assert not plan.additions
    assert plan.ambiguous[0]["reason"] == "duplicate_name_or_multiple_areas"


def test_manual_mapping_resolves_duplicate() -> None:
    plan = build_plan(
        [
            AlexaEndpoint("one", "Lampada"),
            AlexaEndpoint("two", "Lampada"),
        ],
        [AlexaGroup("group", "Studio", frozenset())],
        [HomeAssistantCandidate("light.lampada", "Lampada", "Studio")],
        {"light.lampada": "two"},
    )
    assert [item.endpoint_id for item in plan.additions] == ["two"]


def test_missing_group_is_reported_for_creation() -> None:
    plan = build_plan(
        [AlexaEndpoint("endpoint-1", "Lampada")],
        [],
        [HomeAssistantCandidate("light.lampada", "Lampada", "Studio")],
    )
    assert plan.missing_alexa_groups == ["Studio"]
    assert plan.as_dict()["groups_to_create"] == ["Studio"]
    assert plan.as_dict()["pending_additions"][0]["endpoint_name"] == "Lampada"
    assert plan.as_dict()["change_count"] == 2


def test_duplicate_group_is_ambiguous_and_not_created() -> None:
    plan = build_plan(
        [AlexaEndpoint("endpoint-1", "Lampada")],
        [
            AlexaGroup("group-1", "Studio", frozenset()),
            AlexaGroup("group-2", "Studio", frozenset()),
        ],
        [HomeAssistantCandidate("light.lampada", "Lampada", "Studio")],
    )
    assert plan.missing_alexa_groups == []
    assert plan.additions == []
    assert plan.ambiguous[0]["reason"] == "duplicate_alexa_groups"


def test_source_entity_id_wins_over_ambiguous_display_name() -> None:
    plan = build_plan(
        [
            AlexaEndpoint(
                "endpoint-1",
                "Garage",
                source_entity_id="binary_sensor.garage",
            )
        ],
        [AlexaGroup("group-1", "Cortile", frozenset())],
        [
            HomeAssistantCandidate("binary_sensor.garage", "Garage", "Cortile"),
            HomeAssistantCandidate("camera.garage", "Garage", "Garage"),
        ],
    )
    assert plan.ambiguous == []
    assert plan.additions[0].group_name == "Cortile"


def test_unmatched_alexa_keeps_identifying_metadata() -> None:
    plan = build_plan(
        [
            AlexaEndpoint(
                "endpoint-1",
                "Switch3",
                category="SWITCH",
                model="MatterHub",
                source_entity_id="switch.presa_3",
            )
        ],
        [],
        [],
    )
    unmatched = plan.unmatched_alexa[0]
    assert unmatched == {
        "endpoint_id": "endpoint-1",
        "name": "Switch3",
        "category": "SWITCH",
        "model": "MatterHub",
        "manufacturer": "",
        "description": "",
        "source_provider": "",
        "source_entity_id": "switch.presa_3",
        "source_kind": "ha_entity_id",
        "reason": "source_entity_not_in_managed_ha_area_and_no_exact_name_match",
    }


def test_moves_endpoint_out_of_safe_italian_legacy_room_alias() -> None:
    plan = build_plan(
        [AlexaEndpoint("echo", "Echo Show Camera Letto")],
        [
            AlexaGroup("legacy", "Camera da letto", frozenset({"echo"})),
            AlexaGroup("canonical", "Camera Letto", frozenset()),
        ],
        [
            HomeAssistantCandidate(
                "media_player.echo_show_camera_letto",
                "Echo Show Camera Letto",
                "Camera Letto",
            )
        ],
    )
    assert [item.group_id for item in plan.removals] == ["legacy"]
    assert [item.group_id for item in plan.additions] == ["canonical"]


def test_unrelated_alexa_group_is_never_managed_as_room_alias() -> None:
    plan = build_plan(
        [AlexaEndpoint("echo", "Echo Show Camera Letto")],
        [
            AlexaGroup("functional", "Tutti gli Echo", frozenset({"echo"})),
            AlexaGroup("canonical", "Camera Letto", frozenset()),
        ],
        [
            HomeAssistantCandidate(
                "media_player.echo_show_camera_letto",
                "Echo Show Camera Letto",
                "Camera Letto",
            )
        ],
    )
    assert plan.removals == []
    assert [item.group_id for item in plan.additions] == ["canonical"]


def test_room_audit_reports_echo_in_legacy_room() -> None:
    mismatches, device_issues, inventory = build_room_audit(
        [
            AlexaEndpoint(
                "echo",
                "Echo Show Camera Letto",
                category="ALEXA_VOICE_ENABLED",
                manufacturer="Amazon",
            )
        ],
        [
            AlexaGroup("legacy", "Camera da letto", frozenset({"echo"})),
            AlexaGroup("canonical", "Camera Letto", frozenset()),
        ],
        [
            HomeAssistantCandidate(
                "media_player.echo_show_camera_letto",
                "Echo Show Camera Letto",
                "Camera Letto",
            )
        ],
    )
    assert mismatches[0]["suggested_ha_area"] == "Camera Letto"
    assert mismatches[0]["safe_alias"] is True
    assert device_issues == [
        {
            "endpoint_id": "echo",
            "endpoint_name": "Echo Show Camera Letto",
            "current_alexa_group": "Camera da letto",
            "expected_ha_area": "Camera Letto",
            "already_in_target": False,
        }
    ]
    assert inventory == [
        {
            "endpoint_id": "echo",
            "endpoint_name": "Echo Show Camera Letto",
            "category": "ALEXA_VOICE_ENABLED",
            "expected_ha_area": "Camera Letto",
            "alexa_room_groups": ["Camera da letto"],
            "other_alexa_groups": [],
            "status": "aligned",
        }
    ]


def test_room_audit_reports_echo_in_wrong_exact_room() -> None:
    _, device_issues, inventory = build_room_audit(
        [
            AlexaEndpoint(
                "echo",
                "Echo Show Cucina",
                category="CAMERA",
                manufacturer="Amazon",
            )
        ],
        [
            AlexaGroup("kitchen", "Cucina", frozenset()),
            AlexaGroup("bedroom", "Camera Letto", frozenset({"echo"})),
        ],
        [
            HomeAssistantCandidate(
                "media_player.echo_show_cucina",
                "Echo Show Cucina",
                "Cucina",
            ),
            HomeAssistantCandidate(
                "light.camera",
                "Luce Camera",
                "Camera Letto",
            ),
        ],
    )
    assert inventory[0]["status"] == "wrong_or_multiple_room"
    assert device_issues[0]["expected_ha_area"] == "Cucina"
