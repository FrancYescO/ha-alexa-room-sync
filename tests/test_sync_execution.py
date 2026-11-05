"""Exercise successful and interrupted write sequences with a simulated Alexa API."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from custom_components.alexa_room_sync.api import AlexaApiError
from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup, SyncPlan
from custom_components.alexa_room_sync.runtime import AlexaRoomSyncRuntime


class Api:
    def __init__(self):
        self.groups = {
            "g": AlexaGroup("g", "Old", frozenset({"old", "manual"})),
            "dest": AlexaGroup("dest", "New", frozenset()),
        }
        self.calls = []
        self.fail = None

    async def async_list_groups(self):
        return list(self.groups.values())

    async def async_list_endpoints(self):
        return [AlexaEndpoint("old", "Light", model="MatterHub")]

    async def async_create_group(self, name):
        self.calls.append(("CREATE", name))
        self.groups["created"] = AlexaGroup("created", name, frozenset())

    async def async_update_group(self, group_id, member, operation):
        self.calls.append((operation, group_id, member))
        if operation == self.fail:
            raise AlexaApiError("simulated failure")
        group = self.groups[group_id]
        members = set(group.member_ids)
        members.add(member) if operation == "ADD" else members.discard(member)
        self.groups[group_id] = AlexaGroup(group_id, group.name, frozenset(members))

    async def async_rename_group(self, group, name, endpoints):
        self.calls.append(("RENAME", group.group_id, name))

    async def async_delete_group(self, group, endpoints):
        self.calls.append(("DELETE", group.group_id))
        self.groups.pop(group.group_id)


class Store:
    def __init__(self):
        self.snapshots = []

    async def async_save(self, data):
        self.snapshots.append(deepcopy(data))


def service():
    return AlexaRoomSyncRuntime(Api(), {}, frozenset({"MatterHub"}), store=Store())


def result():
    return {"created_groups": [], "completed": [], "applied_change_count": 0}


def test_all_destinations_are_added_before_any_source_removal():
    runtime = service()
    runtime.records = {
        "area:a": {"name": "Old", "group_id": "g", "members": ["old"]},
        "area:b": {"name": "New", "group_id": "dest", "members": []},
    }
    actions = [
        {
            "key": "area:a",
            "kind": "update",
            "name": "Old",
            "group_id": "g",
            "members": [],
            "remove": ["old"],
            "add": [],
        },
        {
            "key": "area:b",
            "kind": "update",
            "name": "New",
            "group_id": "dest",
            "members": ["old"],
            "remove": [],
            "add": ["old"],
        },
    ]
    asyncio.run(
        runtime._execute(actions, [], list(runtime.api.groups.values()), result())
    )
    assert runtime.api.calls == [("ADD", "dest", "old"), ("REMOVE", "g", "old")]
    assert runtime.api.groups["g"].member_ids == {"manual"}
    assert runtime.records["area:b"]["members"] == ["old"]


def test_failed_addition_never_removes_source_membership():
    runtime = service()
    runtime.api.fail = "ADD"
    runtime.records = {"area:a": {"name": "Old", "group_id": "g", "members": ["old"]}}
    actions = [
        {
            "key": "area:a",
            "kind": "update",
            "name": "Old",
            "group_id": "g",
            "members": ["new"],
            "remove": ["old"],
            "add": ["new"],
        }
    ]
    with pytest.raises(AlexaApiError):
        asyncio.run(
            runtime._execute(actions, [], list(runtime.api.groups.values()), result())
        )
    assert runtime.api.groups["g"].member_ids == {"old", "manual"}
    assert runtime.records["area:a"]["members"] == ["old"]


def test_partial_failure_persists_successful_addition():
    runtime = service()
    runtime.api.fail = "REMOVE"
    runtime.records = {"area:a": {"name": "Old", "group_id": "g", "members": ["old"]}}
    actions = [
        {
            "key": "area:a",
            "kind": "update",
            "name": "Old",
            "group_id": "g",
            "members": ["new"],
            "remove": ["old"],
            "add": ["new"],
        }
    ]
    with pytest.raises(AlexaApiError):
        asyncio.run(
            runtime._execute(actions, [], list(runtime.api.groups.values()), result())
        )
    assert runtime.store.snapshots[-1]["records"]["area:a"]["members"] == ["new", "old"]
    assert runtime.api.groups["g"].member_ids == {"new", "old", "manual"}


def test_new_group_id_is_read_back_and_saved_before_membership_update():
    runtime = service()
    action = {
        "key": "area:c",
        "kind": "create",
        "name": "Kitchen",
        "members": ["light"],
        "add": ["light"],
        "remove": [],
    }
    outcome = result()
    asyncio.run(
        runtime._execute([action], [], list(runtime.api.groups.values()), outcome)
    )
    assert runtime.api.calls == [("CREATE", "Kitchen"), ("ADD", "created", "light")]
    assert runtime.records["area:c"]["group_id"] == "created"
    assert runtime.records["area:c"]["members"] == ["light"]
    assert any(
        s["records"]["area:c"].get("pending_create") for s in runtime.store.snapshots
    )
    assert outcome["applied_change_count"] == 2


def test_deletion_checks_for_new_manual_members_again():
    runtime = service()
    runtime.records = {"area:a": {"name": "Old", "group_id": "g", "members": ["old"]}}
    action = {
        "key": "area:a",
        "kind": "delete",
        "name": "Old",
        "group_id": "g",
        "remove": ["old"],
        "retire_since": 0,
    }
    with pytest.raises(AlexaApiError):
        asyncio.run(
            runtime._execute([action], [], list(runtime.api.groups.values()), result())
        )
    assert runtime.api.calls == []


def test_retiring_group_with_manual_member_is_released_without_deleting_it():
    runtime = service()
    runtime.records = {"area:a": {"name": "Old", "group_id": "g", "members": ["old"]}}
    action = {
        "key": "area:a",
        "kind": "release",
        "name": "Old",
        "group_id": "g",
        "remove": ["old"],
        "retire_since": 0,
    }
    asyncio.run(
        runtime._execute([action], [], list(runtime.api.groups.values()), result())
    )
    assert runtime.api.groups["g"].member_ids == {"manual"}
    assert not runtime.records


@pytest.mark.parametrize(
    "dry_run,allow_writes", [(True, False), (True, True), (False, False)]
)
def test_either_write_gate_disables_all_mutations(dry_run, allow_writes, monkeypatch):
    runtime = service()
    runtime.options.update(dry_run=dry_run, allow_writes=allow_writes)
    monkeypatch.setattr(AlexaRoomSyncRuntime, "_plan", lambda *args: SyncPlan())
    monkeypatch.setattr(
        AlexaRoomSyncRuntime, "_publish", lambda self, hass, result: None
    )
    outcome = asyncio.run(runtime.async_apply(NS()))
    assert outcome["mode"] == "preview"
    assert runtime.api.calls == []


def test_empty_inventory_blocks_sync_instead_of_retiring_groups():
    runtime = service()

    async def empty():
        return []

    runtime.api.async_list_endpoints = empty
    with pytest.raises(AlexaApiError):
        asyncio.run(runtime._inventory(None))


def test_pending_create_recovers_without_creating_a_duplicate():
    runtime = service()
    runtime.records = {
        "area:a": {
            "group_id": None,
            "name": "New",
            "pending_create": True,
            "previous_ids": ["g"],
            "members": [],
        }
    }
    asyncio.run(runtime._inventory(None))
    assert runtime.records["area:a"]["group_id"] == "dest"
    assert not runtime.records["area:a"]["pending_create"]
    assert not runtime.api.calls


def test_missing_tracking_can_be_explicitly_reset_without_alexa_writes():
    runtime = service()
    runtime.records = {
        "area:a": {"name": "Never created", "group_id": None, "pending_create": True}
    }
    result = asyncio.run(runtime.async_forget_missing_groups(["area:a"]))
    assert result == {"forgotten_keys": ["area:a"], "forgotten_count": 1}
    assert not runtime.records and not runtime.api.calls


def test_tracking_reset_refuses_existing_groups_by_either_id_or_name():
    runtime = service()
    runtime.records = {"area:a": {"name": "Old", "group_id": "different-id"}}
    with pytest.raises(ValueError):
        asyncio.run(runtime.async_forget_missing_groups(["area:a"]))
    runtime.records["area:a"] = {"name": "Different name", "group_id": "g"}
    with pytest.raises(ValueError):
        asyncio.run(runtime.async_forget_missing_groups(["area:a"]))
    assert "area:a" in runtime.records and not runtime.api.calls
