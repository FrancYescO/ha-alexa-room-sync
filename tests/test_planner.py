"""Pure planner tests; run inside a Home Assistant development environment."""

from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup
from custom_components.alexa_room_sync.planner import (
    HomeAssistantCandidate,
    build_plan,
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
