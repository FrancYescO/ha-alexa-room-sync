"""Ownership, scope, ambiguity and destructive lifecycle guards."""

from copy import deepcopy

import pytest

from custom_components.alexa_room_sync.managed import reconcile
from custom_components.alexa_room_sync.models import AlexaGroup


def group(name="Kitchen", members=("light", "manual"), group_id="g"):
    return AlexaGroup(group_id, name, frozenset(members))


def record(name="Kitchen", members=("light",), group_id="g", **extra):
    return {"group_id": group_id, "name": name, "members": list(members), **extra}


def room(name="Kitchen", members=("light",)):
    return {"name": name, "members": set(members)}


def test_adopts_exact_unique_group_without_owning_manual_members():
    (action,) = reconcile({"area:a": room()}, [group()], {}, 1000)
    assert action["kind"] == "adopt"
    assert action["members"] == ["light"]
    assert action["remove"] == []


def test_rename_uses_stable_area_identity_and_keeps_manual_members():
    (action,) = reconcile(
        {"area:a": room("Dining", ("new",))}, [group()], {"area:a": record()}, 1000
    )
    assert action["rename"] is True
    assert action["group_id"] == "g"
    assert action["add"] == ["new"]
    assert action["remove"] == ["light"]


@pytest.mark.parametrize(
    "members,kind", [(("light",), "delete"), (("light", "manual"), "release")]
)
def test_retirement_requires_a_later_run_and_preserves_outsiders(members, kind):
    records = {"area:a": record()}
    original = deepcopy(records)
    (action,) = reconcile({}, [group(members=members)], records, 1000)
    assert records == original  # previews never arm removal
    assert action["kind"] == kind
    assert action["held"]
    records["area:a"]["retire_since"] = 1000
    assert reconcile({}, [group(members=members)], records, 1899)[0]["held"]
    assert not reconcile({}, [group(members=members)], records, 1900)[0]["held"]
    assert "manual" not in action["remove"]


def test_bulk_retirement_remains_blocked_even_after_grace():
    records = {f"area:{i}": record(group_id=str(i), retire_since=0) for i in range(4)}
    groups = [group(group_id=str(i)) for i in range(4)]
    assert all(
        a["held"] and a["reason"] == "bulk_removal_guard"
        for a in reconcile({}, groups, records, 2000)
    )


def test_half_of_six_managed_groups_is_also_held():
    records = {f"area:{i}": record(group_id=str(i)) for i in range(6)}
    groups = [group(name=str(i), group_id=str(i)) for i in range(6)]
    rooms = {f"area:{i}": room(name=str(i)) for i in range(3)}
    retired = [
        a for a in reconcile(rooms, groups, records, 2000) if a["kind"] == "release"
    ]
    assert len(retired) == 3
    assert all(a["held"] for a in retired)


def test_single_area_never_retires_or_updates_another_group():
    actions = reconcile(
        {"area:a": room()},
        [group(), group(group_id="other")],
        {"area:other": record(group_id="other", retire_since=0)},
        2000,
        "a",
    )
    # The duplicate target is blocked; there are still no actions for the other area.
    assert all(a["key"] == "area:a" for a in actions)


@pytest.mark.parametrize(
    "groups,rooms,records,reason",
    [
        (
            [group(), group(group_id="duplicate")],
            {"area:a": room()},
            {},
            "duplicate_or_claimed_group",
        ),
        ([group()], {"area:a": room(), "floor:f": room()}, {}, "duplicate_target_name"),
        (
            [group("Old"), group("Kitchen", group_id="other")],
            {"area:a": room()},
            {"area:a": record(name="Old")},
            "rename_name_collision",
        ),
        (
            [group(group_id="other")],
            {"area:a": room()},
            {"area:a": record()},
            "managed_group_missing",
        ),
    ],
)
def test_ambiguous_and_missing_groups_are_never_created_or_deleted(
    groups, rooms, records, reason
):
    actions = reconcile(rooms, groups, records, 1000)
    assert actions
    assert all(a["kind"] == "blocked" and a["reason"] == reason for a in actions)


def test_unmanaged_groups_are_not_adopted_for_deletion():
    assert reconcile({}, [group()], {}, 1000) == []
