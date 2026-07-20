"""Tests for notification summaries."""

from custom_components.alexa_room_sync.notifications import (
    format_preview_notification,
    format_sync_notification,
)


def test_preview_notification_lists_differences() -> None:
    message = format_preview_notification(
        {
            "change_count": 2,
            "groups_to_create": ["Studio"],
            "pending_additions": [
                {"endpoint_name": "Lampada", "group_name": "Studio"}
            ],
            "ambiguous": [{"entity_id": "light.tavolo", "reason": "duplicate"}],
        }
    )
    assert "2 modifiche" in message
    assert "Studio" in message
    assert "Lampada → Studio" in message
    assert "light.tavolo: duplicate" in message


def test_preview_notification_reports_clean_state() -> None:
    assert "già sincronizzata" in format_preview_notification({"change_count": 0})


def test_sync_notification_lists_applied_changes() -> None:
    message = format_sync_notification(
        {
            "applied_change_count": 2,
            "created_groups": ["Studio"],
            "completed": [{"endpoint_name": "Lampada", "group_name": "Studio"}],
        }
    )
    assert "2 modifiche applicate" in message
    assert "Lampada → Studio" in message


def test_preview_notification_identifies_unmatched_as_alexa_side() -> None:
    message = format_preview_notification(
        {
            "change_count": 0,
            "unmatched_alexa": [
                {
                    "endpoint_id": "endpoint-1",
                    "name": "Switch3",
                    "category": "SWITCH",
                    "model": "MatterHub",
                    "source_entity_id": "switch.presa_3",
                    "source_kind": "ha_entity_id",
                    "reason": "source_entity_not_in_managed_ha_area_and_no_exact_name_match",
                }
            ],
        }
    )
    assert "presenti su Alexa" in message
    assert "Alexa · Switch3" in message
    assert "modello: MatterHub" in message
    assert "`switch.presa_3`" in message
    assert "senza stanza" in message
