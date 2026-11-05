"""Pure reconciliation for explicitly tracked area and floor groups.

Lifecycle design inspired by scyto/ha-alexa-group-sync (MIT); see NOTICE.
Only members recorded as managed, or proven matches during adoption, are removed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .models import AlexaGroup
from .planner import normalize_name

REMOVAL_GRACE = 900


def reconcile(
    rooms: dict[str, dict],
    groups: list[AlexaGroup],
    records: dict[str, dict],
    now: float,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Produce deterministic actions without mutating the ownership ledger."""
    by_id = {g.group_id: g for g in groups}
    by_name: dict[str, list[AlexaGroup]] = {}
    for group in groups:
        by_name.setdefault(normalize_name(group.name), []).append(group)
    names = Counter(normalize_name(room["name"]) for room in rooms.values())
    claimed = {r["group_id"] for r in records.values()}
    actions = []
    for key, room in sorted(rooms.items()):
        if scope and key != f"area:{scope}":
            continue
        record = records.get(key)
        group = by_id.get(record["group_id"]) if record else None
        action = {
            "key": key,
            "name": room["name"],
            "members": sorted(room["members"]),
            "group_id": group.group_id if group else None,
        }
        if names[normalize_name(room["name"])] > 1:
            actions.append(
                {**action, "kind": "blocked", "reason": "duplicate_target_name"}
            )
            continue
        if record and group is None:
            actions.append(
                {**action, "kind": "blocked", "reason": "managed_group_missing"}
            )
            continue
        adopting = False
        if group is None:
            matches = by_name.get(normalize_name(room["name"]), [])
            if len(matches) > 1 or (matches and matches[0].group_id in claimed):
                actions.append(
                    {
                        **action,
                        "kind": "blocked",
                        "reason": "duplicate_or_claimed_group",
                    }
                )
                continue
            if matches:
                group = matches[0]
                adopting = True
                action["group_id"] = group.group_id
                claimed.add(group.group_id)
        if group is None:
            if room["members"]:
                actions.append(
                    {**action, "kind": "create", "add": action["members"], "remove": []}
                )
            continue
        collisions = [
            g
            for g in by_name.get(normalize_name(room["name"]), [])
            if g.group_id != group.group_id
        ]
        if collisions:
            actions.append(
                {**action, "kind": "blocked", "reason": "rename_name_collision"}
            )
            continue
        owned = set(record.get("members", [])) if record else set(room["members"])
        additions = set(room["members"]) - group.member_ids
        removals = (owned & group.member_ids) - set(room["members"])
        actions.append(
            {
                **action,
                "kind": "adopt" if adopting else "update",
                "old_name": group.name,
                "add": sorted(additions),
                "remove": sorted(removals),
                "rename": group.name != room["name"],
            }
        )
    if scope:
        return actions
    retiring = []
    for key, record in sorted(records.items()):
        if key in rooms:
            continue
        group = by_id.get(record["group_id"])
        if group is None:
            # A missing group is never proof that its record can be discarded.
            actions.append(
                {
                    "key": key,
                    "name": record["name"],
                    "kind": "blocked",
                    "group_id": record["group_id"],
                    "reason": "managed_group_missing",
                }
            )
            continue
        owned = set(record.get("members", []))
        outsiders = group.member_ids - owned
        due = record.get("retire_since")
        retiring.append(
            {
                "key": key,
                "name": group.name,
                "group_id": group.group_id,
                "kind": "release" if outsiders else "delete",
                "add": [],
                "remove": sorted(group.member_ids & owned),
                "retire_since": now if due is None else due,
                "held": due is None or now - due < REMOVAL_GRACE,
            }
        )
    bulk = len(retiring) > 3 or (
        len(records) >= 6 and len(retiring) * 2 >= len(records)
    )
    for action in retiring:
        if bulk:
            action.update(held=True, reason="bulk_removal_guard")
        elif action["held"]:
            action["reason"] = "removal_grace_period"
    return actions + retiring
