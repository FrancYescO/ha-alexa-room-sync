"""Human-readable persistent notification summaries."""

from __future__ import annotations

from typing import Any


def format_preview_notification(result: dict[str, Any]) -> str:
    """Format a synchronization preview for Home Assistant."""
    change_count = int(result.get("change_count", 0))
    lines = [
        (
            "La configurazione è già sincronizzata."
            if change_count == 0
            else f"Trovate **{change_count} modifiche** da applicare."
        )
    ]
    sections = (
        ("Stanze da creare", result.get("groups_to_create", []), _name),
        ("Entità da aggiungere", result.get("additions", []), _operation),
        ("Aggiunte dopo la creazione stanza", result.get("pending_additions", []), _operation),
        ("Entità da rimuovere", result.get("removals", []), _operation),
        ("Mapping ambigui (non modificati)", result.get("ambiguous", []), _ambiguous),
        ("Endpoint Alexa non abbinati", result.get("unmatched_alexa", []), _name),
    )
    for title, items, formatter in sections:
        if not items:
            continue
        lines.extend((f"\n**{title} ({len(items)})**", *[f"- {formatter(item)}" for item in items]))
    return "\n".join(lines)


def format_sync_notification(result: dict[str, Any]) -> str:
    """Format a completed synchronization for Home Assistant."""
    count = int(result.get("applied_change_count", 0))
    groups = result.get("created_groups", [])
    operations = result.get("completed", [])
    lines = [f"Sincronizzazione completata: **{count} modifiche applicate**."]
    if groups:
        lines.extend(("\n**Stanze create**", *[f"- {_name(item)}" for item in groups]))
    if operations:
        lines.extend(("\n**Membership aggiornate**", *[f"- {_operation(item)}" for item in operations]))
    return "\n".join(lines)


def _name(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name") or item.get("endpoint_name") or item)
    return str(item)


def _operation(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    endpoint = item.get("endpoint_name", "Endpoint")
    group = item.get("group_name", "stanza sconosciuta")
    return f"{endpoint} → {group}"


def _ambiguous(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    name = item.get("endpoint_name") or item.get("entity_id") or "Mapping"
    return f"{name}: {item.get('reason', 'motivo sconosciuto')}"
