"""HA exposure, Echo identity and floor-aware desired group inventory."""

from __future__ import annotations

from collections import defaultdict

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import AlexaApiError
from .planner import HomeAssistantCandidate, normalize_name


def exposed_entities(hass) -> set[str]:
    """Read exposure only when the HA Cloud backend was explicitly enabled."""
    from homeassistant.components.homeassistant.exposed_entities import (
        async_should_expose,
    )

    try:
        return {
            entity_id
            for entity_id in er.async_get(hass).entities
            if async_should_expose(hass, "cloud.alexa", entity_id)
            and cloud_supported(hass, entity_id)
        }
    except Exception as err:
        raise AlexaApiError(
            "Impossibile leggere le entità esposte ad Alexa: sincronizzazione bloccata"
        ) from err


def wanted(entity_id, name, options):
    if entity_id.startswith("climate.") and not options.get("include_climate", True):
        return False
    return options.get("include_child_locks", False) or not (
        "child_lock" in entity_id or "child lock" in name.casefold()
    )


def build_rooms(hass, candidates, mapped, options, exposed, scope):
    area_reg = ar.async_get(hass)
    areas = {area.id: area for area in area_reg.async_list_areas()}
    if scope and scope not in areas:
        raise ValueError("Area Home Assistant inesistente")
    by_name = defaultdict(set)
    for candidate in candidates:
        by_name[candidate.area_name].add(candidate.area_id)
    prefix = options.get("group_prefix", "")
    excluded = set(options.get("excluded_areas", []))
    rooms = {}
    matched_names = {m["group_name"] for m in mapped}
    duplicate_names = [name for name in matched_names if len(by_name[name]) != 1]
    if duplicate_names:
        raise AlexaApiError(
            "Nomi area non univoci: " + ", ".join(sorted(duplicate_names))
        )
    entity_ids_by_name = defaultdict(set)
    for candidate in candidates:
        if wanted(candidate.entity_id, candidate.name, options):
            entity_ids_by_name[candidate.area_name].add(candidate.entity_id)
    # Scope is applied after matching against the entire house, preserving ambiguity checks.
    for match in mapped:
        area_id = next(iter(by_name[match["group_name"]]))
        if scope and area_id != scope:
            continue
        if not scope and area_id in excluded:
            continue
        key = f"area:{area_id}"
        room = rooms.setdefault(
            key,
            {
                "name": prefix + areas[area_id].name,
                "members": set(),
                "light_members": set(),
                "area_id": area_id,
            },
        )
        room["members"].add(match["endpoint_id"])
        if match.get("echo"):
            room.setdefault("echo_members", set()).add(match["endpoint_id"])
        # Exact source matching is available through candidates for the normal matcher.
        ids = match.get("entity_ids", entity_ids_by_name[match["group_name"]])
        if ids and all(e.startswith(("light.", "switch.")) for e in ids):
            room["light_members"].add(match["endpoint_id"])
    if options.get("floor_groups") and not scope:
        from homeassistant.helpers import floor_registry as fr

        floors = fr.async_get(hass)
        area_group_names = {normalize_name(r["name"]) for r in rooms.values()}
        for room in list(rooms.values()):
            floor_id = getattr(areas[room["area_id"]], "floor_id", None)
            floor = floors.async_get_floor(floor_id) if floor_id else None
            if floor is None or normalize_name(prefix + floor.name) in area_group_names:
                continue
            members = (
                room["light_members"]
                if options.get("floor_lights_only", True)
                else room["members"]
            )
            # Echos are excluded even when the floor allows all smart-home domains.
            members = set(members) - set(room.get("echo_members", []))
            if members:
                target = rooms.setdefault(
                    f"floor:{floor_id}", {"name": prefix + floor.name, "members": set()}
                )
                target["members"].update(members)
    present = set()
    for match in mapped:
        present.update(match.get("entity_ids", []))
    missing = {
        c.entity_id: c.name
        for c in candidates
        if c.entity_id in exposed
        and c.entity_id not in present
        and wanted(c.entity_id, c.name, options)
        and (not scope or c.area_id == scope)
        and (scope or c.area_id not in excluded)
    }
    return rooms, missing


def echo_mappings(hass, endpoints, candidates, entry_id, email):
    """Match physical Echo serials within the authenticated account; block ambiguity."""
    device_reg, entity_reg = dr.async_get(hass), er.async_get(hass)
    area_names = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
    accounts = set()
    if entry_id:
        accounts.add(entry_id)
    for domain in ("alexa_devices", "alexa_media"):
        for entry in hass.config_entries.async_entries(domain):
            account = entry.data.get("email") or entry.data.get("username")
            if email and account and account.casefold() == email.casefold():
                accounts.add(entry.entry_id)
    if not accounts:
        return {}, set(), []
    by_serial = defaultdict(list)
    # Entity areas override device areas. Devices without media players still qualify.
    for device in device_reg.devices.values():
        if not accounts.intersection(device.config_entries):
            continue
        model = str(device.model or "").casefold()
        if any(
            token in model
            for token in ("mobile", "multiroom", "stereo", "application", "alexa app")
        ):
            continue
        entities = [
            e
            for e in entity_reg.entities.values()
            if e.device_id == device.id
            and e.entity_id.startswith("media_player.")
            and e.disabled_by is None
            and e.config_entry_id in accounts
        ]
        area_ids = (
            {e.area_id or device.area_id for e in entities}
            if entities
            else {device.area_id}
        )
        area_ids.discard(None)
        serials = {
            str(value).split("_", 1)[0].upper()
            if domain == "alexa_media"
            else str(value).upper()
            for domain, value in device.identifiers
            if domain in ("alexa_media", "alexa_devices")
        }
        for area_id in area_ids:
            if area_id not in area_names:
                continue
            entity_id = entities[0].entity_id if entities else f"device:{device.id}"
            if not entities:
                candidates.append(
                    HomeAssistantCandidate(
                        entity_id,
                        device.name_by_user or device.name or entity_id,
                        area_names[area_id],
                        area_id,
                    )
                )
            for serial in serials:
                by_serial[serial].append(
                    (entity_id, area_id, entry_id in device.config_entries)
                )
    mappings, blocked, issues = {}, set(), []
    serial_endpoints = defaultdict(list)
    for endpoint in endpoints:
        if (endpoint.category or "").casefold() != "alexa_voice_enabled":
            continue
        for serial in endpoint.serials:
            serial_endpoints[serial.upper()].append(endpoint)
    for serial, targets in serial_endpoints.items():
        matches = by_serial.get(serial, [])
        preferred = [m for m in matches if m[2]]
        matches = preferred or matches
        if not matches:
            continue
        if (
            len({m[1] for m in matches}) != 1
            or len({t.endpoint_id for t in targets}) != 1
        ):
            blocked.update(t.endpoint_id for t in targets)
            issues.append(
                {
                    "reason": "ambiguous_echo_serial",
                    "endpoint_ids": sorted(t.endpoint_id for t in targets),
                }
            )
            continue
        mappings[matches[0][0]] = targets[0].endpoint_id
    return mappings, blocked, issues


def cloud_supported(hass, entity_id):
    """Avoid repeatedly reporting Alexa-incompatible entities as missing."""
    try:
        from homeassistant.components.cloud.alexa_config import entity_supported

        return bool(entity_supported(hass, entity_id))
    except Exception:  # noqa: BLE001 - optional private helper, conservative domain fallback
        return entity_id.split(".", 1)[0] in {
            "climate",
            "cover",
            "fan",
            "humidifier",
            "light",
            "lock",
            "media_player",
            "scene",
            "script",
            "switch",
            "vacuum",
            "water_heater",
            "input_boolean",
        }
