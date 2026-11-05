# Alexa Room Sync

[![Validate](https://github.com/FrancYescO/ha-alexa-room-sync/actions/workflows/validate.yml/badge.svg)](https://github.com/FrancYescO/ha-alexa-room-sync/actions/workflows/validate.yml)
[![HACS custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<p align="center">
  <img src="custom_components/alexa_room_sync/brand/logo.png" width="220" alt="Alexa Room Sync logo">
</p>

<p align="center">
  A Home Assistant custom integration that synchronizes Home Assistant areas
  with Alexa rooms and automatically creates missing Alexa groups.
</p>

> [!WARNING]
> Alexa Room Sync uses private, undocumented Alexa GraphQL endpoints. Amazon can
> change or remove them at any time. This project is not affiliated with or
> endorsed by Amazon, Alexa, Nabu Casa, or Home Assistant.

## Features

- Creates an Alexa room when a matched Home Assistant area does not exist.
- Adds matched Alexa endpoints to their correct room.
- Removes endpoints only from other groups whose names match HA areas.
- Reports every Alexa group whose name does not exactly match an HA area.
- Recognizes high-confidence Italian legacy aliases that differ only by
  articles or prepositions (`Camera da letto` → `Camera Letto`).
- Reports Echo/Alexa devices still assigned to one of those legacy rooms.
- Synchronizes Echo devices when their Alexa name has one exact, unique match
  with an Alexa Media Player entity assigned to an HA area.
- Never modifies unrelated Alexa groups such as music or functional groups.
- Synchronizes one area or the whole house, with a persistent area selector.
- Optionally creates floor groups, with lights and switches only by default.
- Tracks managed groups by stable HA area/floor IDs, follows renames, and retires
  obsolete groups after a guarded waiting period while preserving manual members.
- Offers opt-in automatic sync (60-second debounce plus hourly reconciliation).
- Supports HA entity aliases and account-scoped Echo serial-number matching.
- Provides configurable area exclusions, group prefixes, climate/child-lock filters,
  HA Cloud matching, and optional rate-limited resend of missing Cloud devices.
- Exposes a persistent last-result sensor, Activity entries, and redacted diagnostics.
- Uses the MatterHub source `entity_id` when Alexa exposes it as the endpoint
  serial number.
- Falls back to conservative normalized-name matching.
- Blocks duplicate names, duplicate groups, and multi-area ambiguity.
- Limits smart-home synchronization to configured endpoint models; Echo/Amazon
  devices and explicit manual mappings remain eligible.
- Provides a read-only preview before applying changes.
- Exposes Verify, Synchronize, stale-endpoint preview, and guarded cleanup buttons.
- Publishes persistent notifications with verification differences and sync results.
- Reuses live authentication from Alexa Devices or Alexa Media Player.
- Supports a captured HAR cookie as an independent fallback.

## Requirements

- Home Assistant 2026.3 or newer.
- Devices already exposed to Alexa, for example through
  [Home Assistant Matter Hub](https://github.com/RiDDiX/home-assistant-matter-hub/).
- A working built-in **Alexa Devices** account or
  [Alexa Media Player](https://github.com/alandtse/alexa_media_player)
  configuration for shared authentication.

## Installation with HACS

Until the repository is included in the default HACS catalog:

1. Open HACS in Home Assistant.
2. Select **Custom repositories**.
3. Add `https://github.com/FrancYescO/ha-alexa-room-sync` as an **Integration**.
4. Install **Alexa Room Sync**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration** and select
   **Alexa Room Sync**.

[Open this repository in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=FrancYescO&repository=ha-alexa-room-sync&category=integration)

## Manual installation

Copy `custom_components/alexa_room_sync` into the Home Assistant
`/config/custom_components/` directory and restart Home Assistant.

## Authentication

### Alexa Devices

The built-in Home Assistant **Alexa Devices** integration is supported. Select
its loaded account during setup; Alexa Room Sync borrows the current authenticated
HTTP wrapper without storing a second password. The regional API host must match
your Amazon account. Like the Alexa Media Player adapter, this uses private
integration interfaces and may need updates when Home Assistant changes them.

### Alexa Media Player

Alexa Room Sync follows the same runtime-sharing pattern used by companion
integrations such as SmartThings Extra. It obtains the loaded Alexa Media
Player config entry and reuses `entry.runtime_data.login_obj`, including its
current session and cookie store.

Benefits:

- no second Amazon login;
- no HAR capture;
- no duplicate password, OTP secret, or cookie stored by Alexa Room Sync;
- Alexa Media Player reauthentication is automatically picked up.

Alexa Media Player must be loaded before Alexa Room Sync. If it requires
reauthentication, Alexa Room Sync waits instead of silently falling back to a
stale credential.

### Manual cookie fallback

If Alexa Media Player is unavailable, Alexa Room Sync can use the authenticated
cookie from a recent Alexa app request. The capture technique is based on the
reverse-engineering work documented by
[Shereef/Python-Delete-Alexa-Devices](https://github.com/Shereef/Python-Delete-Alexa-Devices).
That project is also a useful reference for regional hosts and HTTP-capture
troubleshooting.

> [!CAUTION]
> An Alexa cookie grants access to your Amazon account. Capture it only on a
> trusted device, never publish it, never attach a HAR file to an issue, and
> remove the capture from the sniffer when configuration is complete.

1. Install an HTTPS traffic-capture tool. The referenced project documents
   HTTP Catcher or Proxyman on iOS and HTTP Toolkit on Android. Capturing the
   Alexa Android app can require certificate-pinning workarounds or a rooted
   test environment.
2. Sign in to the Alexa mobile app with the account used by your devices.
3. Open **Devices**, start the capture, and pull down to refresh the device
   list. Opening a room or device can help generate the required requests.
4. Stop the capture and filter requests for your regional Alexa API host. Look
   first for:

   ```text
   POST /nexus/v1/graphql
   ```

   If it is not present, locate `GET /api/behaviors/entities` as described by
   the referenced project; it can be used to identify the authenticated host
   and cookie from the same Alexa session.
5. In the request headers, copy the complete value of `Cookie`. Paste only the
   value after `Cookie:`, preserving every `name=value` pair and semicolon.
6. Record the request origin as the regional API host, including `https://` but
   no path. For example:

   ```text
   https://eu-api-alexa.amazon.it
   ```

7. In Home Assistant, go to **Settings → Devices & services → Add
   integration → Alexa Room Sync** and select **Cookie da HAR**.
8. Paste the regional host and cookie. Leave **Additional headers JSON** as
   `{}` initially. If Amazon rejects the request, copy only required extra
   headers such as `x-amzn-alexa-app`, `User-Agent`, or `csrf`:

   ```json
   {
     "x-amzn-alexa-app": "captured value",
     "User-Agent": "captured value",
     "csrf": "captured value"
   }
   ```

   Do not add `Cookie`, `Host`, `Content-Length`, `Content-Type`, or `Accept`
   there; Alexa Room Sync manages those headers itself.
9. Submit the form. Alexa Room Sync immediately tests the session by reading
   the Alexa groups. An authentication error normally means the cookie has
   expired, the host belongs to a different Amazon region, or a required
   captured header is missing.

The standalone deletion script also asks for a skill identifier and separate
CSRF value. Those are script-specific: Alexa Room Sync does not require a skill
identifier during setup. Cookies expire and must be captured again when Amazon
invalidates the session.

## Usage

Synchronization starts in **preview-only mode**, including after upgrading an
existing installation. In **Configure**, turn off **Preview only** and enable
**I understand synchronization can create, rename and delete managed Alexa groups**
to allow synchronization writes. Stale-endpoint deletion has its own existing
preview/confirmation flow and is independent of these synchronization options.

The integration creates six button entities on its device:

- **One area: preview** and **One area: synchronize** use the persistent
  **Area to synchronize** selector. The selector follows area renames.

- **Verify synchronization** performs a read-only comparison and creates a
  persistent Home Assistant notification listing all differences and blocked
  mappings.
- **Synchronize now** creates missing rooms, updates memberships, and creates a
  notification summarizing the applied changes.
- **Verify stale endpoints** lists deletable Home Assistant skill endpoints and
  arms that exact selection for 10 minutes.
- **Delete stale endpoints** deletes only the selection armed by the latest
  preview, after rereading Alexa and revalidating every safety guard.

The same operations remain available as actions for automations and scripts.

Run the read-only preview from **Developer tools → Actions**:

```yaml
action: alexa_room_sync.preview
response_variable: preview
```

The response includes:

- `groups_to_create`: missing Alexa rooms;
- `room_name_mismatches`: Alexa groups without an exact HA area name;
- `alexa_device_room_issues`: Echo/Alexa devices found in a safely recognized
  legacy room name;
- `alexa_device_room_inventory`: all Amazon/Echo endpoints with their Alexa
  room memberships, expected HA area, and alignment status;
- `pending_additions`: endpoints waiting for room creation;
- `additions` and `removals`: membership changes;
- `ambiguous`: mappings intentionally blocked;
- `unmatched_alexa`: endpoints without an HA match;
- `change_count`: total planned changes.

Apply a fresh plan (or return a preview when writes are disabled):

```yaml
action: alexa_room_sync.apply
response_variable: result
```

Alexa Room Sync rereads both systems immediately before applying, creates
missing rooms, rereads the Alexa-assigned group IDs, and then performs
membership updates serially. It emits `alexa_room_sync_finished` when done.

### Settings and automatic synchronization

| Option | Default | Behavior |
|---|---|---|
| Preview only / allow writes | On / Off | Both gates must allow writes. |
| Automatic sync | Off | Runs 60 seconds after registry changes and hourly; respects write gates. |
| Include Echo devices | On | Stable serial matching when the authenticated account is known; conservative name matching remains available. |
| Floor groups | Off | Includes devices from synced areas; Echos remain in area groups. |
| Floor lights and switches only | On | Excludes climate and other domains from floor commands. |
| Climate / child-lock switches | On / Off | Controls membership eligibility. |
| HA Cloud devices | Off | Adds explicitly exposed HA skill devices alongside MatterHub. |
| Resend missing Cloud devices | Off | Requires Cloud matching; waits 10 minutes, then sends add-or-update reports at most hourly. Never toggles exposure. |
| Group prefix | Empty | Applies to area and floor names. |
| Areas to skip | Empty | Excludes areas from whole-house and floor sync; retires their tracked groups. |

HA Cloud matching is optional: MatterHub installations do not need HA Cloud.
A failure to read Cloud exposure settings blocks synchronization instead of
interpreting the failure as an empty inventory.

Sync one area with the selected HA area ID:

```yaml
action: alexa_room_sync.sync_area
data:
  area_id: kitchen
response_variable: result
```

`preview` and `apply` also accept an optional `area_id`. A scoped sync changes
only that area's group; it can explicitly target a skipped area and never updates
floor groups or removes membership from another area. A later whole-house sync
reconciles moves between areas and updates floors. Matching still considers the
whole house so narrowing the scope cannot hide ambiguous names.

The **Last result** sensor persists the latest outcome and detailed result across
restarts. Applied changes also appear in Activity. Diagnostics omit credentials,
account identifiers, serials and device names. Use **Reconfigure** to change the
session, host, endpoint models or explicit mappings.

### Managed group lifecycle

The ownership ledger lives in `.storage/alexa_room_sync.<entry_id>.groups`.
Groups created by the integration, and uniquely named existing groups matching a
desired area/floor, are tracked by stable registry ID. Only proven matching members
are adopted; other manually added members remain untouched. A rename updates the
existing group rather than recreating it.

A tracked group whose area/floor is removed, excluded, or no longer has eligible
matched devices is marked for retirement on a write-enabled run. Deletion or
release requires a later write-enabled run at least **15 minutes** afterward.
Previews never arm retirement. More than three retirements in a run, or at least
half of six or more managed groups, blocks all retirements for that run. Groups
with manual members are released after managed members are removed, preserving
the group and manual devices. Alexa routines referencing a deleted group can break.

To retire all tracked groups explicitly:

```yaml
action: alexa_room_sync.remove_managed_groups
data:
  confirm: true
response_variable: result
```

This action respects the same write gates, waiting period and bulk-removal guard.
For a large installation, retire areas in smaller batches using the exclusion
option, rather than bypassing the guard. Removing the integration only deletes its
local ledger; it never writes to Alexa.

Empty Alexa inventories, missing tracked group IDs, name collisions and ambiguous
mappings block unsafe operations. Successful writes are saved incrementally. If
creation succeeds but its ID is not yet visible, a pending record prevents duplicate
creation; a later preview can recover the newly assigned ID. If identity cannot be
proven, the group remains blocked for inspection.

If a failed create never produced a group, or a tracked group was deliberately
removed outside the integration, use `alexa_room_sync.forget_missing_groups` with
`confirm: true` and an explicit `group_keys` list copied from `group_actions.key`
in the preview. This resets only the selected local records, after rereading Alexa
and proving neither the saved ID nor name is present. It never modifies Alexa; the
next enabled sync can create the required group again. Do not reset tracking while
a newly created group is still propagating through Amazon's API.

Group rename/delete uses the Phoenix API only after establishing its relationship
to the GraphQL group, by identical ID or unique name plus a complete matching
membership inventory. An unprovable cross-API identity blocks the operation.

### Cleaning stale Home Assistant skill endpoints

Alexa can retain devices previously exposed by the Home Assistant Alexa Smart
Home skill after their HA entities have been removed. Preview only endpoints
that are proven stale:

```yaml
action: alexa_room_sync.cleanup_preview
response_variable: cleanup
```

Stale-endpoint deletion is never part of room synchronization. In the UI, first press
**Verify stale endpoints**, inspect its notification, then press **Delete stale
endpoints** within 10 minutes. The action API also allows deleting an explicit
selection by copying only the desired `endpoint_id` values from the preview:

```yaml
action: alexa_room_sync.delete_stale_endpoints
data:
  endpoint_ids:
    - amzn1.alexa.endpoint.example
  confirm: true
response_variable: deleted
```

Immediately before each request, the integration verifies again that the
endpoint belongs to the Home Assistant skill, exposes a source HA `entity_id`,
has a legacy Alexa appliance identifier, and that the entity no longer exists
in either the HA entity registry or state machine. Active or unrelated devices
are rejected.

For safety, opaque Alexa serial numbers are never accepted as proof that a Home
Assistant entity was removed. Cleanup requires an unambiguous
`domain.object_id` source identifier.

## Matching and safety model

The preferred mapping is Alexa endpoint serial number → HA `entity_id`. When
that identity is unavailable, matching compares the Alexa name against the HA
entity name, original name, state friendly name, and device names while
ignoring case, accents, and repeated whitespace.

An automatic match must resolve to one endpoint and one HA area. Ambiguous
items remain untouched and can be resolved using an explicit
`entity_id` → Alexa `endpoint_id` mapping.

## Known limitations

- The Alexa GraphQL API is private and may change without notice.
- Alexa Media Player is itself an unofficial integration.
- Alexa may take a few seconds to expose a newly created group.
- Rename/delete requires a provable GraphQL-to-Phoenix group identity; some account/API shapes may be blocked.
- A duplicate or ambiguous identity is reported rather than guessed.
- Native Amazon API behavior still needs validation against each regional account.
- Authentication and API behavior may differ between Amazon regions.

## Development

```bash
python -m compileall custom_components/alexa_room_sync
pytest -q
# Optional end-to-end smoke test with real Home Assistant (Python 3.14+):
python tests/ha_smoke.py
```

Pull requests are welcome. Please avoid including cookies, HAR files, email
addresses, endpoint IDs, or other account-specific data in tests and reports.

The early Git history was reconstructed from preserved release archives. See
[HISTORY.md](HISTORY.md) for provenance and limitations.

## Credits and license

Additional functionality was informed by
[scyto/ha-alexa-group-sync](https://github.com/scyto/ha-alexa-group-sync).
Adapted code and its upstream MIT notice are documented in [NOTICE](NOTICE).

[MIT](LICENSE)
