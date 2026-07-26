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
        (
            "Stanze Alexa con nome diverso da Home Assistant",
            result.get("room_name_mismatches", []),
            _room_mismatch,
        ),
        (
            "Dispositivi Alexa in stanze con nome non allineato",
            result.get("alexa_device_room_issues", []),
            _alexa_device_room_issue,
        ),
        (
            "Inventario stanze dispositivi Alexa",
            result.get("alexa_device_room_inventory", []),
            _alexa_device_room_inventory,
        ),
        ("Mapping ambigui (non modificati)", result.get("ambiguous", []), _ambiguous),
        (
            "Endpoint presenti su Alexa ma non associati a Home Assistant",
            result.get("unmatched_alexa", []),
            _unmatched_alexa,
        ),
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


def format_cleanup_preview_notification(items: list[dict[str, Any]]) -> str:
    """Format the guarded stale-endpoint cleanup preview."""
    if not items:
        return "Non sono stati trovati endpoint Home Assistant obsoleti su Alexa."
    lines = [
        f"Trovati **{len(items)} endpoint obsoleti**. Controlla l'elenco; il pulsante "
        "**Elimina endpoint obsoleti** rimane abilitato per questa verifica per 10 minuti."
    ]
    lines.extend(
        f"- **{item.get('endpoint_name') or 'Endpoint senza nome'}** — "
        f"sorgente HA: `{item.get('entity_id') or 'sconosciuta'}`"
        for item in items
    )
    return "\n".join(lines)


def format_cleanup_delete_notification(result: dict[str, Any]) -> str:
    """Format a completed guarded stale-endpoint deletion."""
    items = result.get("deleted", [])
    count = int(result.get("deleted_count", 0))
    lines = [f"Pulizia completata: **{count} endpoint obsoleti eliminati**."]
    lines.extend(
        f"- **{item.get('endpoint_name') or 'Endpoint senza nome'}** — "
        f"sorgente HA: `{item.get('entity_id') or 'sconosciuta'}`"
        for item in items
    )
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


def _unmatched_alexa(item: Any) -> str:
    """Describe an Alexa-side endpoint that has no safe HA match."""
    if not isinstance(item, dict):
        return f"Alexa: {item}"

    name = item.get("name") or "Endpoint senza nome"
    details = ["origine: Alexa"]
    if item.get("category"):
        details.append(f"categoria: {item['category']}")
    if item.get("model"):
        details.append(f"modello: {item['model']}")
    if item.get("manufacturer"):
        details.append(f"produttore: {item['manufacturer']}")
    if item.get("source_provider"):
        details.append(f"provider: {item['source_provider']}")

    source = item.get("source_entity_id")
    source_kind = item.get("source_kind")
    if source_kind == "ha_entity_id" and source:
        details.append(
            f"entity_id sorgente: `{source}` (non trovato tra le entità HA attive con stanza)"
        )
    elif source:
        details.append(f"seriale/sorgente Alexa: `{source}`")
    else:
        details.append("entity_id sorgente: non esposto da Alexa")

    if item.get("endpoint_id"):
        details.append(f"endpoint_id Alexa: `{item['endpoint_id']}`")

    reason = item.get("reason")
    if reason == "source_entity_not_in_managed_ha_area_and_no_exact_name_match":
        details.append("motivo: entity_id HA assente, disabilitato o senza stanza")
    else:
        details.append("motivo: nessun nome identico tra le entità HA con stanza")
    return f"**Alexa · {name}** — " + "; ".join(details)


def _room_mismatch(item: Any) -> str:
    """Describe an Alexa group whose name differs from HA."""
    if not isinstance(item, dict):
        return str(item)
    name = item.get("alexa_group_name") or "Gruppo senza nome"
    suggested = item.get("suggested_ha_area")
    members = int(item.get("member_count", 0))
    if suggested:
        return f"**{name}** → possibile area HA **{suggested}**; membri: {members}"
    return f"**{name}** — nessuna corrispondenza sicura; membri: {members}"


def _alexa_device_room_issue(item: Any) -> str:
    """Describe an Echo/Alexa device in a mismatched room."""
    if not isinstance(item, dict):
        return str(item)
    return (
        f"**{item.get('endpoint_name') or 'Dispositivo Alexa'}**: "
        f"{item.get('current_alexa_group') or 'stanza sconosciuta'} → "
        f"{item.get('expected_ha_area') or 'area sconosciuta'}"
    )


def _alexa_device_room_inventory(item: Any) -> str:
    """Describe the room assignment of one Amazon/Alexa device."""
    if not isinstance(item, dict):
        return str(item)
    rooms = ", ".join(item.get("alexa_room_groups", [])) or "nessuna stanza"
    expected = item.get("expected_ha_area") or "nessuna corrispondenza HA"
    return (
        f"**{item.get('endpoint_name') or 'Dispositivo Alexa'}** — "
        f"Alexa: {rooms}; HA: {expected}; stato: {item.get('status', 'sconosciuto')}"
    )
