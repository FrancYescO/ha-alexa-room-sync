"""Build conservative Alexa room synchronization plans."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .const import UPDATE_ADD, UPDATE_REMOVE
from .models import AlexaEndpoint, AlexaGroup, Operation, SyncPlan

_ITALIAN_ROOM_GLUE_WORDS = frozenset(
    {
        "a",
        "al",
        "alla",
        "alle",
        "da",
        "dal",
        "dalla",
        "dalle",
        "de",
        "dei",
        "del",
        "della",
        "delle",
        "di",
        "il",
        "i",
        "la",
        "le",
        "lo",
        "gli",
    }
)


@dataclass(frozen=True, slots=True)
class HomeAssistantCandidate:
    """A Home Assistant name associated with one effective area."""

    entity_id: str
    name: str
    area_name: str


def normalize_name(value: str) -> str:
    """Normalize user-facing names for conservative exact matching."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_room_key(value: str) -> str:
    """Normalize a room name while ignoring Italian articles/prepositions."""
    return " ".join(
        token
        for token in normalize_name(value).split()
        if token not in _ITALIAN_ROOM_GLUE_WORDS
    )


def build_room_audit(
    endpoints: Iterable[AlexaEndpoint],
    groups: Iterable[AlexaGroup],
    candidates: Iterable[HomeAssistantCandidate],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Report non-matching Alexa room names and affected Alexa devices."""
    endpoint_list = list(endpoints)
    group_list = list(groups)
    candidate_list = list(candidates)
    endpoints_by_id = {item.endpoint_id: item for item in endpoint_list}
    candidate_areas_by_name: dict[str, set[str]] = defaultdict(set)
    candidate_area_by_entity: dict[str, str] = {}
    for candidate in candidate_list:
        candidate_areas_by_name[normalize_name(candidate.name)].add(
            candidate.area_name
        )
        candidate_area_by_entity[candidate.entity_id] = candidate.area_name

    area_names = sorted({item.area_name for item in candidate_list})
    areas_by_normalized = {normalize_name(name): name for name in area_names}
    areas_by_room_key: dict[str, list[str]] = defaultdict(list)
    for area_name in area_names:
        areas_by_room_key[normalize_room_key(area_name)].append(area_name)

    exact_group_by_area: dict[str, list[AlexaGroup]] = defaultdict(list)
    group_area_by_id: dict[str, str] = {}
    for group in group_list:
        normalized = normalize_name(group.name)
        if normalized in areas_by_normalized:
            exact_group_by_area[normalized].append(group)
            group_area_by_id[group.group_id] = areas_by_normalized[normalized]

    safe_aliases = _safe_legacy_group_aliases(group_list, candidate_list)
    for group in group_list:
        alias_area = safe_aliases.get(normalize_name(group.name))
        if alias_area:
            group_area_by_id[group.group_id] = alias_area

    mismatches: list[dict[str, Any]] = []
    device_issues: list[dict[str, Any]] = []
    for group in sorted(group_list, key=lambda item: normalize_name(item.name)):
        normalized = normalize_name(group.name)
        if normalized in areas_by_normalized:
            continue

        suggested_areas = areas_by_room_key.get(normalize_room_key(group.name), [])
        suggested_area = suggested_areas[0] if len(suggested_areas) == 1 else None
        target_groups = (
            exact_group_by_area.get(normalize_name(suggested_area), [])
            if suggested_area
            else []
        )
        target_group = target_groups[0] if len(target_groups) == 1 else None
        members = [
            {
                "endpoint_id": endpoint_id,
                "endpoint_name": (
                    endpoints_by_id[endpoint_id].name
                    if endpoint_id in endpoints_by_id
                    else "Endpoint non disponibile"
                ),
                "category": (
                    endpoints_by_id[endpoint_id].category or ""
                    if endpoint_id in endpoints_by_id
                    else ""
                ),
                "manufacturer": (
                    endpoints_by_id[endpoint_id].manufacturer or ""
                    if endpoint_id in endpoints_by_id
                    else ""
                ),
                "already_in_target": bool(
                    target_group and endpoint_id in target_group.member_ids
                ),
            }
            for endpoint_id in sorted(group.member_ids)
        ]
        mismatch = {
            "group_id": group.group_id,
            "alexa_group_name": group.name,
            "member_count": len(group.member_ids),
            "members": members,
            "suggested_ha_area": suggested_area or "",
            "suggested_target_group_id": target_group.group_id if target_group else "",
            "reason": (
                "italian_glue_words_only"
                if suggested_area
                else "no_exact_or_safe_ha_area_match"
            ),
            "safe_alias": bool(suggested_area and target_group),
        }
        mismatches.append(mismatch)

        if not suggested_area:
            continue
        for member in members:
            category = str(member["category"]).casefold()
            manufacturer = str(member["manufacturer"]).casefold()
            if category != "alexa_voice_enabled" and manufacturer != "amazon":
                continue
            device_issues.append(
                {
                    "endpoint_id": member["endpoint_id"],
                    "endpoint_name": member["endpoint_name"],
                    "current_alexa_group": group.name,
                    "expected_ha_area": suggested_area,
                    "already_in_target": member["already_in_target"],
                }
            )

    groups_by_member: dict[str, list[AlexaGroup]] = defaultdict(list)
    for group in group_list:
        for endpoint_id in group.member_ids:
            groups_by_member[endpoint_id].append(group)

    inventory: list[dict[str, Any]] = []
    issue_ids = {str(item["endpoint_id"]) for item in device_issues}
    for endpoint in sorted(endpoint_list, key=lambda item: normalize_name(item.name)):
        category = (endpoint.category or "").casefold()
        manufacturer = (endpoint.manufacturer or "").casefold()
        if category != "alexa_voice_enabled" and manufacturer != "amazon":
            continue

        candidate_areas = set(
            candidate_areas_by_name.get(normalize_name(endpoint.name), set())
        )
        source_area = candidate_area_by_entity.get(endpoint.source_entity_id or "")
        if source_area:
            candidate_areas.add(source_area)
        expected_area = next(iter(candidate_areas)) if len(candidate_areas) == 1 else ""

        memberships = sorted(
            groups_by_member.get(endpoint.endpoint_id, []),
            key=lambda item: normalize_name(item.name),
        )
        room_memberships = [
            group for group in memberships if group.group_id in group_area_by_id
        ]
        current_areas = sorted(
            {group_area_by_id[group.group_id] for group in room_memberships}
        )
        expected_present = bool(expected_area and expected_area in current_areas)
        wrong_areas = [
            area_name for area_name in current_areas if area_name != expected_area
        ]
        status = (
            "no_unique_ha_match"
            if not expected_area
            else "wrong_or_multiple_room"
            if not expected_present or wrong_areas
            else "aligned"
        )
        inventory_item = {
            "endpoint_id": endpoint.endpoint_id,
            "endpoint_name": endpoint.name,
            "category": endpoint.category or "",
            "expected_ha_area": expected_area,
            "alexa_room_groups": [group.name for group in room_memberships],
            "other_alexa_groups": [
                group.name
                for group in memberships
                if group.group_id not in group_area_by_id
            ],
            "status": status,
        }
        inventory.append(inventory_item)

        if status == "wrong_or_multiple_room" and endpoint.endpoint_id not in issue_ids:
            device_issues.append(
                {
                    "endpoint_id": endpoint.endpoint_id,
                    "endpoint_name": endpoint.name,
                    "current_alexa_group": ", ".join(
                        group.name for group in room_memberships
                    ),
                    "expected_ha_area": expected_area,
                    "already_in_target": expected_present,
                    "reason": "wrong_or_multiple_room",
                }
            )

    return mismatches, device_issues, inventory


def _safe_legacy_group_aliases(
    groups: Iterable[AlexaGroup],
    candidates: Iterable[HomeAssistantCandidate],
) -> dict[str, str]:
    """Map high-confidence legacy Alexa group names to canonical HA areas."""
    area_names = sorted({item.area_name for item in candidates})
    areas_by_room_key: dict[str, list[str]] = defaultdict(list)
    exact_area_names = {normalize_name(name) for name in area_names}
    for area_name in area_names:
        areas_by_room_key[normalize_room_key(area_name)].append(area_name)

    aliases: dict[str, str] = {}
    for group in groups:
        normalized = normalize_name(group.name)
        if normalized in exact_area_names:
            continue
        matches = areas_by_room_key.get(normalize_room_key(group.name), [])
        if len(matches) == 1:
            aliases[normalized] = matches[0]
    return aliases


def build_plan(
    endpoints: Iterable[AlexaEndpoint],
    groups: Iterable[AlexaGroup],
    candidates: Iterable[HomeAssistantCandidate],
    manual_mappings: Mapping[str, str] | None = None,
) -> SyncPlan:
    """Build a plan without guessing across duplicate names or areas."""
    plan = SyncPlan()
    endpoint_list = list(endpoints)
    group_list = list(groups)
    candidate_list = list(candidates)
    manual_mappings = manual_mappings or {}

    endpoints_by_name: dict[str, list[AlexaEndpoint]] = defaultdict(list)
    endpoints_by_id = {item.endpoint_id: item for item in endpoint_list}
    for endpoint in endpoint_list:
        endpoints_by_name[normalize_name(endpoint.name)].append(endpoint)

    candidates_by_name: dict[str, list[HomeAssistantCandidate]] = defaultdict(list)
    candidates_by_entity = {item.entity_id: item for item in candidate_list}
    for candidate in candidate_list:
        candidates_by_name[normalize_name(candidate.name)].append(candidate)

    groups_by_name: dict[str, list[AlexaGroup]] = defaultdict(list)
    for group in group_list:
        groups_by_name[normalize_name(group.name)].append(group)

    desired_area_by_endpoint: dict[str, str] = {}

    for entity_id, endpoint_id in manual_mappings.items():
        candidate = candidates_by_entity.get(entity_id)
        endpoint = endpoints_by_id.get(endpoint_id)
        if candidate is None or endpoint is None:
            plan.ambiguous.append(
                {
                    "reason": "invalid_manual_mapping",
                    "entity_id": entity_id,
                    "endpoint_id": endpoint_id,
                }
            )
            continue
        desired_area_by_endpoint[endpoint_id] = candidate.area_name

    # MatterHub exposes the originating Home Assistant entity_id as the Alexa
    # endpoint serial number. Prefer this stable identity over display names.
    for endpoint in endpoint_list:
        if endpoint.endpoint_id in desired_area_by_endpoint:
            continue
        candidate = candidates_by_entity.get(endpoint.source_entity_id or "")
        if candidate is not None:
            desired_area_by_endpoint[endpoint.endpoint_id] = candidate.area_name

    manually_mapped_ids = set(desired_area_by_endpoint)
    manually_mapped_names = {
        normalize_name(endpoints_by_id[endpoint_id].name)
        for endpoint_id in manually_mapped_ids
    }
    for normalized, same_name_endpoints in endpoints_by_name.items():
        if normalized in manually_mapped_names:
            continue
        remaining = [
            item for item in same_name_endpoints
            if item.endpoint_id not in manually_mapped_ids
        ]
        if not remaining:
            continue
        matching_candidates = candidates_by_name.get(normalized, [])
        areas = {item.area_name for item in matching_candidates}
        if len(remaining) == 1 and len(areas) == 1:
            desired_area_by_endpoint[remaining[0].endpoint_id] = next(iter(areas))
            continue
        if matching_candidates:
            plan.ambiguous.append(
                {
                    "reason": "duplicate_name_or_multiple_areas",
                    "alexa_name": remaining[0].name,
                    "endpoint_ids": sorted(
                        item.endpoint_id for item in remaining
                    ),
                    "endpoint_count": len(remaining),
                    "candidate_entities": sorted(
                        item.entity_id for item in matching_candidates
                    ),
                    "candidate_areas": sorted(areas),
                }
            )
        else:
            plan.unmatched_alexa.extend(
                {
                    "endpoint_id": item.endpoint_id,
                    "name": item.name,
                    "category": item.category or "",
                    "model": item.model or "",
                    "manufacturer": item.manufacturer or "",
                    "description": item.description or "",
                    "source_provider": item.source_provider or "",
                    "source_entity_id": item.source_entity_id or "",
                    "source_kind": (
                        "ha_entity_id"
                        if _looks_like_entity_id(item.source_entity_id)
                        else "alexa_serial"
                        if item.source_entity_id
                        else "missing"
                    ),
                    "reason": (
                        "source_entity_not_in_managed_ha_area_and_no_exact_name_match"
                        if _looks_like_entity_id(item.source_entity_id)
                        else "no_exact_ha_name_match"
                    ),
                }
                for item in remaining
            )

    for endpoint_id, area_name in desired_area_by_endpoint.items():
        endpoint = endpoints_by_id[endpoint_id]
        plan.mapped_count += 1
        matching_groups = groups_by_name.get(normalize_name(area_name), [])
        if not matching_groups:
            plan.missing_alexa_groups.append(area_name)
            plan.pending_additions.append(
                {
                    "endpoint_id": endpoint_id,
                    "endpoint_name": endpoint.name,
                    "group_name": area_name,
                }
            )
            continue
        if len(matching_groups) > 1:
            plan.ambiguous.append(
                {
                    "reason": "duplicate_alexa_groups",
                    "area_name": area_name,
                    "group_ids": sorted(item.group_id for item in matching_groups),
                    "endpoint_id": endpoint_id,
                    "endpoint_name": endpoint.name,
                }
            )
            continue
        desired_group = matching_groups[0]

        # Only groups whose names correspond to HA areas are managed. Other
        # Alexa groups (audio, functional groups, etc.) are never modified.
        # A legacy room name is managed only when removing Italian glue words
        # produces one unique HA area name.
        managed_groups = {
            normalize_name(item.area_name) for item in candidate_list
        }
        legacy_aliases = _safe_legacy_group_aliases(group_list, candidate_list)
        for group in group_list:
            normalized_group = normalize_name(group.name)
            if (
                normalized_group not in managed_groups
                and normalized_group not in legacy_aliases
            ):
                continue
            if group.group_id == desired_group.group_id:
                if endpoint_id not in group.member_ids:
                    plan.additions.append(
                        Operation(
                            UPDATE_ADD,
                            endpoint_id,
                            endpoint.name,
                            group.group_id,
                            group.name,
                        )
                    )
            elif endpoint_id in group.member_ids:
                plan.removals.append(
                    Operation(
                        UPDATE_REMOVE,
                        endpoint_id,
                        endpoint.name,
                        group.group_id,
                        group.name,
                    )
                )

    return plan


def _looks_like_entity_id(value: str | None) -> bool:
    """Return whether an Alexa serial resembles a Home Assistant entity ID."""
    return bool(value and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", value))
