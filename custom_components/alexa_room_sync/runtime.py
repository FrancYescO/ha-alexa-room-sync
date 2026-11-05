"""Runtime orchestration for Alexa Room Sync."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import AlexaApiError, AlexaRoomApi
from .const import DEFAULT_OPTIONS, DOMAIN
from .managed import reconcile
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

    options: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_OPTIONS))
    records: dict[str, dict] = field(default_factory=dict)
    store: Any = None
    entry_id: str = ""
    auth_entry_id: str | None = None
    account_email: str | None = None
    result_entity_id: str | None = None
    selected_area: str | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    missing_since: dict[str, float] = field(default_factory=dict)
    last_resend: float | None = None

    async def async_save(self):
        if self.store:
            await self.store.async_save(
                {
                    "records": self.records,
                    "selected_area": self.selected_area,
                    "last_result": self.last_result,
                }
            )

    def _publish(self, hass, result):
        from homeassistant.helpers.dispatcher import async_dispatcher_send

        self.last_result = {**result, "ran_at": datetime.now(UTC).isoformat()}
        async_dispatcher_send(hass, f"{DOMAIN}_{self.entry_id}_result")

    async def _inventory(self, hass):
        endpoints, groups = await asyncio.gather(
            self.api.async_list_endpoints(), self.api.async_list_groups()
        )
        if not endpoints:
            raise AlexaApiError(
                "Alexa non ha restituito dispositivi: sincronizzazione bloccata"
            )
        if self.records and not groups:
            raise AlexaApiError("Alexa non ha restituito gruppi già gestiti")
        # A create can succeed even if the following read times out. Never create twice.
        for record in self.records.values():
            if record.get("pending_create"):
                found = [
                    g
                    for g in groups
                    if g.name == record["name"]
                    and g.group_id not in record.get("previous_ids", [])
                ]
                if len(found) == 1:
                    record.update(group_id=found[0].group_id, pending_create=False)
        return endpoints, groups

    def _plan(self, hass, all_endpoints, groups, area_id=None, teardown=False):
        from .inventory import build_rooms, echo_mappings, exposed_entities, wanted

        candidates = collect_candidates(hass)
        exposed = exposed_entities(hass) if self.options.get("cloud_devices") else set()
        endpoints = self._filter_endpoints(all_endpoints, exposed)
        excluded_entities = {
            c.entity_id
            for c in candidates
            if not wanted(c.entity_id, c.name, self.options)
        }
        candidates = [c for c in candidates if c.entity_id not in excluded_entities]
        endpoints = [
            e
            for e in endpoints
            if not e.source_entity_id
            or wanted(e.source_entity_id, e.name, self.options)
        ]
        mappings = dict(self.manual_mappings)
        blocked_serials = set()
        serial_issues = []
        if self.options.get("include_echos", True):
            serial_mappings, blocked_serials, serial_issues = echo_mappings(
                hass, endpoints, candidates, self.auth_entry_id, self.account_email
            )
            mappings = {**serial_mappings, **mappings}
        endpoints = [
            e
            for e in endpoints
            if e.endpoint_id not in blocked_serials
            or e.endpoint_id in self.manual_mappings.values()
        ]
        # The original conservative matcher remains the single mapping authority.
        matches = build_plan(endpoints, [], candidates, mappings)
        matches.ambiguous.extend(serial_issues)
        from .planner import normalize_name

        endpoint_by_id = {e.endpoint_id: e for e in endpoints}
        for match in matches.pending_additions:
            endpoint = endpoint_by_id[match["endpoint_id"]]
            explicit = {
                entity
                for entity, target in mappings.items()
                if target == endpoint.endpoint_id
            }
            source = {
                c.entity_id
                for c in candidates
                if c.entity_id == endpoint.source_entity_id
            }
            match["entity_ids"] = sorted(
                explicit
                or source
                or {
                    c.entity_id
                    for c in candidates
                    if c.area_name == match["group_name"]
                    and normalize_name(c.name) == normalize_name(endpoint.name)
                }
            )
            match["echo"] = (
                endpoint.category or ""
            ).casefold() == "alexa_voice_enabled"
        rooms, missing = build_rooms(
            hass, candidates, matches.pending_additions, self.options, exposed, area_id
        )
        if teardown:
            rooms = {}
        actions = reconcile(rooms, groups, self.records, time.time(), area_id)
        if matches.ambiguous:
            # A newly ambiguous mapping must never make a tracked group look obsolete.
            for action in actions:
                if action["kind"] in ("delete", "release") or action.get("remove"):
                    action.update(held=True, reason="ambiguous_mapping")
        plan = build_plan(endpoints, groups, candidates, mappings)
        if not area_id and not teardown:
            managed_ids = {r["group_id"] for r in self.records.values()}
            target_ids = {a.get("group_id") for a in actions}
            legacy_by_group = {}
            from .planner import normalize_room_key

            excluded_ids = set(self.options.get("excluded_areas", []))
            excluded_names = {
                normalize_room_key(area.name)
                for area in ar.async_get(hass).async_list_areas()
                if area.id in excluded_ids
            }
            group_name_counts = Counter(normalize_name(g.name) for g in groups)
            for operation in plan.removals:
                if normalize_room_key(operation.group_name) in excluded_names:
                    continue
                if group_name_counts[normalize_name(operation.group_name)] > 1:
                    matches.ambiguous.append(
                        {
                            "reason": "duplicate_alexa_groups",
                            "group_name": operation.group_name,
                        }
                    )
                    continue
                # Tracked floor memberships may deliberately overlap with rooms.
                if operation.group_id in managed_ids:
                    continue
                existing = next(
                    (
                        a
                        for a in actions
                        if a.get("group_id") == operation.group_id
                        and a["kind"] in ("adopt", "update")
                    ),
                    None,
                )
                if existing:
                    existing["remove"] = sorted(
                        set(existing.get("remove", [])) | {operation.endpoint_id}
                    )
                elif operation.group_id not in target_ids:
                    action = legacy_by_group.setdefault(
                        operation.group_id,
                        {
                            "key": f"legacy:{operation.group_id}",
                            "group_id": operation.group_id,
                            "name": operation.group_name,
                            "kind": "membership",
                            "add": [],
                            "remove": [],
                        },
                    )
                    action["remove"].append(operation.endpoint_id)
            actions.extend(legacy_by_group.values())
        plan.ambiguous = list(matches.ambiguous)
        plan.mapped_count = sum(
            len(room["members"])
            for key, room in rooms.items()
            if key.startswith("area:")
        )
        plan.additions.clear()
        plan.removals.clear()
        plan.pending_additions.clear()
        plan.missing_alexa_groups.clear()
        from .models import Operation

        by_id = {e.endpoint_id: e for e in endpoints}
        for action in actions:
            if action["kind"] == "blocked":
                plan.ambiguous.append(
                    {"reason": action["reason"], "group_name": action["name"]}
                )
            if action["kind"] == "create":
                plan.missing_alexa_groups.append(action["name"])
            if action.get("held") or action["kind"] == "blocked":
                continue
            for kind, operation in (("add", "ADD"), ("remove", "REMOVE")):
                if action["kind"] == "delete":
                    continue
                for endpoint_id in action.get(kind, []):
                    item = {
                        "endpoint_id": endpoint_id,
                        "endpoint_name": by_id[endpoint_id].name
                        if endpoint_id in by_id
                        else endpoint_id,
                        "group_name": action["name"],
                    }
                    if action["kind"] == "create":
                        if kind == "add":
                            plan.pending_additions.append(item)
                    else:
                        getattr(
                            plan, "additions" if kind == "add" else "removals"
                        ).append(
                            Operation(operation, group_id=action["group_id"], **item)
                        )
        plan.group_actions = actions
        plan.missing_in_alexa = missing
        area_names = {c.area_id: c.area_name for c in candidates}
        group_areas = {
            record["group_id"]: area_names[key.removeprefix("area:")]
            for key, record in self.records.items()
            if key.startswith("area:") and key.removeprefix("area:") in area_names
        }
        prefix = self.options.get("group_prefix", "")
        for group in groups:
            found = {
                c.area_name for c in candidates if prefix + c.area_name == group.name
            }
            if len(found) == 1:
                group_areas[group.group_id] = next(iter(found))
        floor_ids = {
            record["group_id"]
            for key, record in self.records.items()
            if key.startswith("floor:")
        }
        endpoint_areas = {
            m["endpoint_id"]: m["group_name"] for m in matches.pending_additions
        }
        (
            plan.room_name_mismatches,
            plan.alexa_device_room_issues,
            plan.alexa_device_room_inventory,
        ) = build_room_audit(
            all_endpoints,
            [g for g in groups if g.group_id not in floor_ids],
            candidates,
            group_areas,
            endpoint_areas,
        )
        return plan

    async def async_plan(self, hass: HomeAssistant, area_id: str | None = None):
        async with self.lock:
            try:
                endpoints, groups = await self._inventory(hass)
                plan = self._plan(hass, endpoints, groups, area_id)
                self._publish(
                    hass,
                    {**plan.as_dict(), "mode": "preview", "scope": area_id or "all"},
                )
                return plan
            except (AlexaApiError, ValueError):
                self._publish(
                    hass,
                    {
                        "mode": "error",
                        "scope": area_id or "all",
                        "error": "Preview failed; no Alexa writes were made",
                    },
                )
                raise
            finally:
                await self.async_save()

    async def async_apply(
        self,
        hass: HomeAssistant,
        area_id: str | None = None,
        teardown: bool = False,
        automatic: bool = False,
    ):
        async with self.lock:
            permitted = not self.options.get("dry_run", True) and self.options.get(
                "allow_writes", False
            )
            result = {
                "mode": "apply" if permitted else "preview",
                "scope": area_id or "all",
                "created_groups": [],
                "completed": [],
                "applied_change_count": 0,
            }
            try:
                endpoints, groups = await self._inventory(hass)
                plan = self._plan(hass, endpoints, groups, area_id, teardown)
                result.update(plan.as_dict())
                if permitted:
                    await self._execute(plan.group_actions, endpoints, groups, result)
                    from .cloud import async_resend_missing

                    if (
                        not teardown
                        and self.options.get("resend_missing")
                        and self.options.get("cloud_devices")
                    ):
                        result["cloud_resend"] = await async_resend_missing(
                            hass, self, plan.missing_in_alexa
                        )
                self._publish(hass, result)
                if permitted and result["applied_change_count"]:
                    from homeassistant.components.logbook import async_log_entry

                    async_log_entry(
                        hass,
                        "Alexa Room Sync",
                        f"{result['applied_change_count']} changes: "
                        + "; ".join(
                            f"{a['operation']} {a['group_name']}"
                            for a in result["completed"]
                        ),
                        domain=DOMAIN,
                        entity_id=self.result_entity_id,
                    )
                return result
            except Exception:
                self._publish(
                    hass,
                    {
                        **result,
                        "mode": "error",
                        "error": "Synchronization interrupted; preview again",
                    },
                )
                raise
            finally:
                await self.async_save()

    async def _execute(self, actions, endpoints, groups, result):
        by_id = {g.group_id: g for g in groups}
        executable = []
        for action in actions:
            kind, key = action["kind"], action["key"]
            if kind == "blocked":
                continue
            if kind in ("delete", "release"):
                self.records[key]["retire_since"] = action["retire_since"]
            elif key in self.records:
                self.records[key].pop("retire_since", None)
            if action.get("held"):
                continue
            group = by_id.get(action.get("group_id"))
            if kind == "create":
                self.records[key] = {
                    "name": action["name"],
                    "group_id": None,
                    "members": [],
                    "pending_create": True,
                    "previous_ids": list(by_id),
                }
                await self.async_save()
                await self.api.async_create_group(action["name"])
                for attempt in range(5):
                    fresh = await self.api.async_list_groups()
                    found = [
                        g
                        for g in fresh
                        if g.name == action["name"] and g.group_id not in by_id
                    ]
                    if len(found) == 1:
                        group = found[0]
                        break
                    if len(found) > 1:
                        raise AlexaApiError("Nuovo gruppo Alexa ambiguo")
                    if attempt < 4:
                        await asyncio.sleep(1)
                if group is None:
                    raise AlexaApiError(
                        "Il nuovo gruppo Alexa non è ancora visibile; riprovare l'anteprima"
                    )
                by_id[group.group_id] = group
                self.records[key].update(group_id=group.group_id, pending_create=False)
                result["created_groups"].append(action["name"])
                self._completed(result, "CREATE", action["name"])
            if kind in ("create", "adopt", "update"):
                if key not in self.records:
                    self.records[key] = {
                        "group_id": group.group_id,
                        "name": group.name,
                        "members": sorted(set(action["members"]) & group.member_ids),
                    }
                await self.async_save()
                if action.get("rename"):
                    await self.api.async_rename_group(group, action["name"], endpoints)
                    self.records[key]["name"] = action["name"]
                    self._completed(result, "RENAME", action["name"])
                for member in action.get("add", []):
                    await self._membership(
                        group.group_id,
                        member,
                        "ADD",
                        key,
                        action["name"],
                        endpoints,
                        result,
                    )
            executable.append((action, group))
        # Every destination must have accepted its additions before ANY source loses a member.
        for action, group in executable:
            kind, key = action["kind"], action["key"]
            if kind == "delete":
                fresh = await self.api.async_list_groups()
                current = next((g for g in fresh if g.group_id == group.group_id), None)
                if current is None or current.member_ids - set(
                    self.records[key].get("members", [])
                ):
                    raise AlexaApiError(
                        "Il gruppo è cambiato dopo la verifica; cancellazione bloccata"
                    )
                await self.api.async_delete_group(current, endpoints)
                self._completed(result, "DELETE", action["name"])
            else:
                for member in action.get("remove", []):
                    await self._membership(
                        group.group_id,
                        member,
                        "REMOVE",
                        key,
                        action["name"],
                        endpoints,
                        result,
                    )
            if kind in ("delete", "release"):
                self.records.pop(key, None)
                if kind == "release":
                    self._completed(result, "RELEASE", action["name"])
            elif key in self.records:
                self.records[key]["members"] = sorted(set(action["members"]))
            await self.async_save()

    @staticmethod
    def _completed(result, operation, group_name, endpoint_name=""):
        result["completed"].append(
            {
                "operation": operation,
                "group_name": group_name,
                "endpoint_name": endpoint_name,
            }
        )
        result["applied_change_count"] += 1

    async def _membership(
        self, group_id, member, operation, key, name, endpoints, result
    ):
        await self.api.async_update_group(group_id, member, operation)
        if key in self.records:
            owned = set(self.records[key].get("members", []))
            owned.add(member) if operation == "ADD" else owned.discard(member)
            self.records[key]["members"] = sorted(owned)
        await self.async_save()
        label = next((e.name for e in endpoints if e.endpoint_id == member), member)
        self._completed(result, operation, name, label)

    async def async_forget_missing_groups(self, keys: list[str]):
        """Explicit local recovery for missing IDs or an unconfirmed failed create.

        Never forget a record while either its ID or its name still exists in Alexa.
        This method performs no Alexa writes.
        """
        from .planner import normalize_name

        async with self.lock:
            groups = await self.api.async_list_groups()
            ids = {group.group_id for group in groups}
            names = {normalize_name(group.name) for group in groups}
            selected = sorted(set(keys))
            for key in selected:
                record = self.records.get(key)
                if (
                    record is None
                    or record.get("group_id") in ids
                    or normalize_name(record["name"]) in names
                ):
                    raise ValueError(
                        "Gruppo ancora presente o record non valido: " + key
                    )
            for key in selected:
                self.records.pop(key)
            await self.async_save()
            return {"forgotten_keys": selected, "forgotten_count": len(selected)}

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
                    "Endpoint non eliminabili o non più obsoleti: "
                    + ", ".join(rejected)
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
                    "Endpoint non eliminabili o non più obsoleti: "
                    + ", ".join(rejected)
                )
            deleted: list[dict[str, Any]] = []
            for endpoint_id in requested:
                item = stale_by_id[endpoint_id]
                await self.api.async_delete_appliance(item["appliance_id"])
                deleted.append(item)
            return {"deleted_count": len(deleted), "deleted": deleted}

    def _filter_endpoints(self, endpoints, exposed=frozenset()):
        """Keep configured models, Echo devices, and explicit mappings."""
        manual_ids = set(self.manual_mappings.values())
        managed_models = {item.casefold() for item in self.endpoint_models}
        return [
            endpoint
            for endpoint in endpoints
            if endpoint.endpoint_id in manual_ids
            or (
                self.options.get("include_echos", True)
                and (
                    (endpoint.category or "").casefold() == "alexa_voice_enabled"
                    or (
                        (endpoint.manufacturer or "").casefold() == "amazon"
                        and (endpoint.category or "").casefold()
                        not in {"application", "hub"}
                    )
                )
            )
            or (
                self.options.get("cloud_devices")
                and endpoint.source_entity_id in exposed
                and (endpoint.manufacturer or "").casefold() == "home assistant"
            )
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
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
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
            device_registry.async_get(entity.device_id) if entity.device_id else None
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
                *(getattr(entity, "aliases", ()) or ()),
            )
            if isinstance(name, str) and name.strip()
        }
        for name in names:
            key = (entity.entity_id, name, area_name)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                HomeAssistantCandidate(entity.entity_id, name, area_name, area_id)
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
