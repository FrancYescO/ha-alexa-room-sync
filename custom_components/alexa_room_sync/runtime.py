"""Runtime orchestration for Alexa Room Sync."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import AlexaRoomApi
from .planner import HomeAssistantCandidate, build_plan


@dataclass(slots=True)
class AlexaRoomSyncRuntime:
    """Per-config-entry runtime state."""

    api: AlexaRoomApi
    manual_mappings: dict[str, str]
    endpoint_models: frozenset[str]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def async_plan(self, hass: HomeAssistant):
        """Read both systems and create a fresh plan."""
        async with self.lock:
            endpoints, groups = await asyncio.gather(
                self.api.async_list_endpoints(), self.api.async_list_groups()
            )
            endpoints = self._filter_endpoints(endpoints)
            candidates = collect_candidates(hass)
            return build_plan(
                endpoints, groups, candidates, self.manual_mappings
            )

    async def async_apply(self, hass: HomeAssistant) -> dict[str, Any]:
        """Re-read, then apply removals before additions serially."""
        async with self.lock:
            endpoints, groups = await asyncio.gather(
                self.api.async_list_endpoints(), self.api.async_list_groups()
            )
            endpoints = self._filter_endpoints(endpoints)
            plan = build_plan(
                endpoints,
                groups,
                collect_candidates(hass),
                self.manual_mappings,
            )
            completed: list[dict[str, str]] = []
            for operation in [*plan.removals, *plan.additions]:
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
            result["completed"] = completed
            return result

    def _filter_endpoints(self, endpoints):
        """Keep configured bridge models plus explicit manual endpoints."""
        manual_ids = set(self.manual_mappings.values())
        allowed = {item.casefold() for item in self.endpoint_models}
        return [
            endpoint
            for endpoint in endpoints
            if endpoint.endpoint_id in manual_ids
            or (endpoint.model or "").casefold() in allowed
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
