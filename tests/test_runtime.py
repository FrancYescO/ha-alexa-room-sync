"""Runtime safety tests without installing Home Assistant."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.alexa_room_sync.models import AlexaEndpoint
from custom_components.alexa_room_sync import runtime


class _Registry:
    def __init__(self, entity_ids: set[str] | None = None) -> None:
        self._entity_ids = entity_ids or set()

    def async_get(self, entity_id: str):
        return object() if entity_id in self._entity_ids else None


class _States:
    def __init__(self, entity_ids: set[str] | None = None) -> None:
        self._entity_ids = entity_ids or set()

    def get(self, entity_id: str):
        return object() if entity_id in self._entity_ids else None


def _hass(
    registry_ids: set[str] | None = None,
    state_ids: set[str] | None = None,
):
    registry = _Registry(registry_ids)
    runtime.er = SimpleNamespace(async_get=lambda hass: registry)
    return SimpleNamespace(states=_States(state_ids))


def test_filter_uses_configured_models_and_documented_exceptions() -> None:
    service = runtime.AlexaRoomSyncRuntime(
        api=None,
        manual_mappings={"light.manual": "manual"},
        endpoint_models=frozenset({"MatterHub"}),
    )
    endpoints = [
        AlexaEndpoint("matter", "Matter light", model="matterhub"),
        AlexaEndpoint("echo", "Echo", category="ALEXA_VOICE_ENABLED"),
        AlexaEndpoint("amazon", "Amazon device", manufacturer="Amazon"),
        AlexaEndpoint("manual", "Manual", category="APPLICATION"),
        AlexaEndpoint("other", "Unrelated", model="OtherBridge"),
    ]

    assert [item.endpoint_id for item in service._filter_endpoints(endpoints)] == [
        "matter",
        "echo",
        "amazon",
        "manual",
    ]


def test_cleanup_rejects_opaque_serial_number() -> None:
    endpoint = AlexaEndpoint(
        "endpoint",
        "Lampada",
        source_entity_id="opaque-device-serial",
        manufacturer="Home Assistant",
        source_provider="SKILL",
        appliance_id="appliance",
    )

    assert runtime._find_stale_home_assistant_endpoints(_hass(), [endpoint]) == []


def test_cleanup_accepts_only_missing_unambiguous_entity() -> None:
    missing = AlexaEndpoint(
        "missing",
        "Lampada rimossa",
        source_entity_id="light.removed",
        manufacturer="Home Assistant",
        source_provider="SKILL",
        appliance_id="appliance-missing",
    )
    active = AlexaEndpoint(
        "active",
        "Lampada attiva",
        source_entity_id="light.active",
        manufacturer="Home Assistant",
        source_provider="SKILL",
        appliance_id="appliance-active",
    )

    result = runtime._find_stale_home_assistant_endpoints(
        _hass(registry_ids={"light.active"}), [missing, active]
    )

    assert [item["endpoint_id"] for item in result] == ["missing"]


def test_cleanup_inventory_is_independent_from_sync_models() -> None:
    service = runtime.AlexaRoomSyncRuntime(
        api=None,
        manual_mappings={},
        endpoint_models=frozenset({"MatterHub"}),
    )
    endpoints = [
        AlexaEndpoint("skill", "HA", category="SWITCH", model="OtherBridge"),
        AlexaEndpoint("app", "App", category="APPLICATION"),
        AlexaEndpoint("hub", "Hub", category="HUB"),
    ]

    assert [
        item.endpoint_id for item in service._filter_cleanup_endpoints(endpoints)
    ] == ["skill"]
