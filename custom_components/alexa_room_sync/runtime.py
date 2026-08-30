"""Runtime orchestration for Alexa Room Sync."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import AlexaRoomApi
from .planner import HomeAssistantCandidate, build_plan, build_room_audit


@dataclass(slots=True)
class AlexaRoomSyncRuntime:
    """Per-config-entry runtime state."""

    api: AlexaRoomApi
    manual_mappings: dict[str, str]
    endpoint_models: frozenset[str]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cleanup_candidate_ids: tuple[str, ...] = ()
    cleanup_prepared_at: float | None = None

    async def async_plan(self, hass: HomeAssistant):
        """Read both systems and create a fresh plan."""
        async with self.lock:
            all_endpoints, groups = await asyncio.gather(
                self.api.async_list_endpoints(), self.api.async_list_groups()
            )
            endpoints = self._filter_endpoints(all_endpoints)
            candidates = collect_candidates(hass)
            plan = build_plan(
                endpoints, groups, candidates, self.manual_mappings
            )
            (
                plan.room_name_mismatches,
                plan.alexa_device_room_issues,
                plan.alexa_device_room_inventory,
            ) = build_room_audit(all_endpoints, groups, candidates)
            return plan

    async def async_apply(self, hass: HomeAssistant) -> dict[str, Any]:
        """Create missing rooms, then apply membership changes serially."""
        async with self.lock:
            all_endpoints, groups = await asyncio.gather(
                self.api.async_list_endpoints(), self.api.async_list_groups()
            )
            endpoints = self._filter_endpoints(all_endpoints)
            candidates = collect_candidates(hass)
            plan = build_plan(
                endpoints,
                groups,
                candidates,
                self.manual_mappings,
            )
            (
                plan.room_name_mismatches,
                plan.alexa_device_room_issues,
                plan.alexa_device_room_inventory,
            ) = build_room_audit(all_endpoints, groups, candidates)
            created_groups: list[str] = []
            for group_name in sorted(set(plan.missing_alexa_groups)):
                await self.api.async_create_group(group_name)
                created_groups.append(group_name)

            # New group IDs are assigned by Alexa. Re-read the groups and
            # rebuild the plan so all following operations use those IDs.
            if created_groups:
                expected_names = {item.casefold() for item in created_groups}
                for attempt in range(5):
                    groups = await self.api.async_list_groups()
                    visible_names = {item.name.casefold() for item in groups}
                    if expected_names <= visible_names:
                        break
                    if attempt < 4:
                        await asyncio.sleep(1)
                plan = build_plan(
                    endpoints,
                    groups,
                    candidates,
                    self.manual_mappings,
                )
                (
                    plan.room_name_mismatches,
                    plan.alexa_device_room_issues,
                    plan.alexa_device_room_inventory,
                ) = build_room_audit(all_endpoints, groups, candidates)

            completed: list[dict[str, str]] = []
            # Add before removing so a mapped endpoint is never left without
            # its intended room if Alexa rejects a later operation.
            for operation in [*plan.additions, *plan.removals]:
                await self.api.async_update_group(
                    operation.group_id,
                    operation.endpoint_id,
                    operation.operation,
                )
                completed.append(
                    {
                        "operation": operation.operation,
                        "endpoint_name": operation.endpoint_name,
                        "group_name": operation.group_name,
                    }
                )
            result = plan.as_dict()
            result["created_groups"] = created_groups
            result["completed"] = completed
            result["applied_change_count"] = len(created_groups) + len(completed)
            return result

    async def async_stale_endpoints(self, hass: HomeAssistant) -> list[dict[str, Any]]:
        """Return deletable Home Assistant endpoints no longer present in HA."""
        async with self.lock:
            endpoints = self._filter_cleanup_endpoints(
                await self.api.async_list_endpoints()
            )
            return _find_stale_home_assistant_endpoints(hass, endpoints)

    async def async_prepare_stale_cleanup(
        self, hass: HomeAssistant
    ) -> list[dict[str, Any]]:
        """Find stale endpoints and arm one guarded deletion attempt."""
        async with self.lock:
            endpoints = self._filter_cleanup_endpoints(
                await self.api.async_list_endpoints()
            )
            stale = _find_stale_home_assistant_endpoints(hass, endpoints)
            self.cleanup_candidate_ids = tuple(item["endpoint_id"] for item in stale)
            self.cleanup_prepared_at = time.monotonic()
            return stale

    async def async_delete_prepared_stale_endpoints(
        self, hass: HomeAssistant, max_age_seconds: int = 600
    ) -> dict[str, Any]:
        """Delete only candidates armed by a recent cleanup preview."""
        async with self.lock:
            if not self.cleanup_prepared_at:
                raise ValueError(
                    "Esegui prima Verifica endpoint obsoleti per preparare la pulizia"
                )
            if time.monotonic() - self.cleanup_prepared_at > max_age_seconds:
                self.cleanup_candidate_ids = ()
                self.cleanup_prepared_at = None
                raise ValueError(
                    "La verifica degli endpoint obsoleti è scaduta; eseguila di nuovo"
                )

            requested = list(self.cleanup_candidate_ids)
            # Consume the preview even if Alexa rejects a request. A retry must
            # always start from a new inventory read and explicit preview.
            self.cleanup_candidate_ids = ()
            self.cleanup_prepared_at = None
            if not requested:
                return {"deleted_count": 0, "deleted": []}

            endpoints = self._filter_cleanup_endpoints(
                await self.api.async_list_endpoints()
            )
            stale = _find_stale_home_assistant_endpoints(hass, endpoints)
            stale_by_id = {item["endpoint_id"]: item for item in stale}
            rejected = [item for item in requested if item not in stale_by_id]
            if rejected:
                raise ValueError(
                    "Endpoint non eliminabili o non più obsoleti: " + ", ".join(rejected)
                )

            deleted: list[dict[str, Any]] = []
            for endpoint_id in requested:
                item = stale_by_id[endpoint_id]
                await self.api.async_delete_appliance(item["appliance_id"])
                deleted.append(item)
            return {"deleted_count": len(deleted), "deleted": deleted}

    async def async_delete_stale_endpoints(
        self, hass: HomeAssistant, endpoint_ids: list[str]
    ) -> dict[str, Any]:
        """Delete explicitly selected endpoints after revalidating every guard."""
        async with self.lock:
            endpoints = self._filter_cleanup_endpoints(
                await self.api.async_list_endpoints()
            )
            stale = _find_stale_home_assistant_endpoints(hass, endpoints)
            stale_by_id = {item["endpoint_id"]: item for item in stale}
            requested = list(dict.fromkeys(endpoint_ids))
            rejected = [item for item in requested if item not in stale_by_id]
            if rejected:
                raise ValueError(
                    "Endpoint non eliminabili o non più obsoleti: " + ", ".join(rejected)
                )
            deleted: list[dict[str, Any]] = []
            for endpoint_id in requested:
                item = stale_by_id[endpoint_id]
                await self.api.async_delete_appliance(item["appliance_id"])
                deleted.append(item)
            return {"deleted_count": len(deleted), "deleted": deleted}

    def _filter_endpoints(self, endpoints):
        """Keep configured models, Echo devices, and explicit mappings."""
        manual_ids = set(self.manual_mappings.values())
        managed_models = {item.casefold() for item in self.endpoint_models}
        return [
            endpoint
            for endpoint in endpoints
            if endpoint.endpoint_id in manual_ids
            or (endpoint.category or "").casefold() == "alexa_voice_enabled"
            or (endpoint.manufacturer or "").casefold() == "amazon"
            or (endpoint.model or "").casefold() in managed_models
        ]

    @staticmethod
    def _filter_cleanup_endpoints(endpoints):
        """Exclude Alexa clients and hubs from the guarded cleanup inventory."""
        excluded_categories = {"application", "hub"}
        return [
            endpoint
            for endpoint in endpoints
            if (endpoint.category or "").casefold() not in excluded_categories
        ]


def parse_string_mapping(raw: str) -> dict[str, str]:
    """Parse a JSON object whose keys and values must be strings."""
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError("È richiesto un oggetto JSON stringa:stringa")
    return value


def parse_string_list(raw: str) -> frozenset[str]:
    """Parse a non-empty JSON array of strings."""
    value = json.loads(raw)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ValueError("È richiesto un array JSON non vuoto di stringhe")
    return frozenset(item.strip() for item in value)


def collect_candidates(hass: HomeAssistant) -> list[HomeAssistantCandidate]:
    """Collect HA names with the effective entity/device area."""
    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    areas = {area.id: area.name for area in area_registry.async_list_areas()}

    candidates: list[HomeAssistantCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for entity in entity_registry.entities.values():
        device = (
            device_registry.async_get(entity.device_id)
            if entity.device_id
            else None
        )
        area_id = entity.area_id or (device.area_id if device else None)
        area_name = areas.get(area_id)
        if not area_name or entity.disabled_by is not None:
            continue

        state = hass.states.get(entity.entity_id)
        names = {
            name.strip()
            for name in (
                entity.name,
                entity.original_name,
                state.attributes.get("friendly_name") if state else None,
                device.name_by_user if device else None,
                device.name if device else None,
            )
            if isinstance(name, str) and name.strip()
        }
        for name in names:
            key = (entity.entity_id, name, area_name)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                HomeAssistantCandidate(entity.entity_id, name, area_name)
            )
    return candidates


def _find_stale_home_assistant_endpoints(
    hass: HomeAssistant, endpoints
) -> list[dict[str, Any]]:
    """Find Alexa appliances proven to originate from removed HA entities."""
    entity_registry = er.async_get(hass)
    stale: list[dict[str, Any]] = []
    for endpoint in endpoints:
        entity_id = endpoint.source_entity_id
        if (
            (endpoint.manufacturer or "").casefold() != "home assistant"
            or endpoint.source_provider != "SKILL"
            or not _is_home_assistant_entity_id(entity_id)
            or not endpoint.appliance_id
            or entity_registry.async_get(entity_id) is not None
            or hass.states.get(entity_id) is not None
        ):
            continue
        stale.append(
            {
                "endpoint_id": endpoint.endpoint_id,
                "endpoint_name": endpoint.name,
                "entity_id": entity_id,
                "manufacturer": endpoint.manufacturer,
                "model": endpoint.model,
                "source_provider": endpoint.source_provider,
                "appliance_id": endpoint.appliance_id,
            }
        )
    return sorted(stale, key=lambda item: (item["entity_id"], item["endpoint_id"]))


def _is_home_assistant_entity_id(value: str | None) -> bool:
    """Return whether a value is unambiguously a Home Assistant entity ID."""
    return bool(value and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", value))
