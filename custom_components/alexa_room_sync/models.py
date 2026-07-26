"""Data models for Alexa Room Sync."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AlexaEndpoint:
    """An Alexa smart-home endpoint."""

    endpoint_id: str
    name: str
    category: str | None = None
    model: str | None = None
    source_entity_id: str | None = None
    manufacturer: str | None = None
    description: str | None = None
    source_provider: str | None = None
    appliance_id: str | None = None


@dataclass(frozen=True, slots=True)
class AlexaGroup:
    """An Alexa device group."""

    group_id: str
    name: str
    member_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class Operation:
    """One membership change."""

    operation: str
    endpoint_id: str
    endpoint_name: str
    group_id: str
    group_name: str


@dataclass(slots=True)
class SyncPlan:
    """A safe, inspectable synchronization plan."""

    additions: list[Operation] = field(default_factory=list)
    removals: list[Operation] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    unmatched_alexa: list[dict[str, str]] = field(default_factory=list)
    missing_alexa_groups: list[str] = field(default_factory=list)
    pending_additions: list[dict[str, str]] = field(default_factory=list)
    room_name_mismatches: list[dict[str, Any]] = field(default_factory=list)
    alexa_device_room_issues: list[dict[str, Any]] = field(default_factory=list)
    alexa_device_room_inventory: list[dict[str, Any]] = field(default_factory=list)
    mapped_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        groups_to_create = sorted(set(self.missing_alexa_groups))
        return {
            "mapped_count": self.mapped_count,
            "additions": [asdict(item) for item in self.additions],
            "removals": [asdict(item) for item in self.removals],
            "ambiguous": self.ambiguous,
            "unmatched_alexa": self.unmatched_alexa,
            "missing_alexa_groups": groups_to_create,
            "groups_to_create": groups_to_create,
            "pending_additions": self.pending_additions,
            "room_name_mismatches": self.room_name_mismatches,
            "alexa_device_room_issues": self.alexa_device_room_issues,
            "alexa_device_room_inventory": self.alexa_device_room_inventory,
            "change_count": (
                len(groups_to_create)
                + len(self.pending_additions)
                + len(self.additions)
                + len(self.removals)
            ),
        }
