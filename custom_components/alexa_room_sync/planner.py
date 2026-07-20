"""Build conservative Alexa room synchronization plans."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .const import UPDATE_ADD, UPDATE_REMOVE
from .models import AlexaEndpoint, AlexaGroup, Operation, SyncPlan


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
        managed_groups = {
            normalize_name(item.area_name) for item in candidate_list
        }
        for group in group_list:
            if normalize_name(group.name) not in managed_groups:
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
