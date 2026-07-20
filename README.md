# Alexa Room Sync

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
- Never modifies unrelated Alexa groups such as music or functional groups.
- Uses the MatterHub source `entity_id` when Alexa exposes it as the endpoint
  serial number.
- Falls back to conservative normalized-name matching.
- Blocks duplicate names, duplicate groups, and multi-area ambiguity.
- Provides a read-only preview before applying changes.
- Exposes Verify and Synchronize button entities in Home Assistant.
- Publishes persistent notifications with verification differences and sync results.
- Reuses the live authentication from Alexa Media Player when available.
- Supports a captured HAR cookie as an independent fallback.

## Requirements

- Home Assistant 2026.3 or newer.
- Devices already exposed to Alexa, for example through
  [Home Assistant Matter Hub](https://github.com/t0bst4r/home-assistant-matter-hub).
- Recommended: a working
  [Alexa Media Player](https://github.com/alandtse/alexa_media_player)
  configuration for shared authentication.

## Installation with HACS

Until the repository is included in the default HACS catalog:

1. Open HACS in Home Assistant.
2. Select **Custom repositories**.
3. Add `https://github.com/FrancYescO/alexa-room-sync` as an **Integration**.
4. Install **Alexa Room Sync**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration** and select
   **Alexa Room Sync**.

## Manual installation

Copy `custom_components/alexa_room_sync` into the Home Assistant
`/config/custom_components/` directory and restart Home Assistant.

## Authentication

### Alexa Media Player — recommended

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

### HAR cookie fallback

If Alexa Media Player is not installed, capture a recent Alexa app request:

```text
POST https://eu-api-alexa.amazon.it/nexus/v1/graphql
```

Provide the complete `Cookie` header and the appropriate regional API host.
The cookie is a credential: never publish HAR files or attach them to issues.

## Usage

The integration creates two button entities on its device:

- **Verify synchronization** performs a read-only comparison and creates a
  persistent Home Assistant notification listing all differences and blocked
  mappings.
- **Synchronize now** creates missing rooms, updates memberships, and creates a
  notification summarizing the applied changes.

The same operations remain available as actions for automations and scripts.

Run the read-only preview from **Developer tools → Actions**:

```yaml
action: alexa_room_sync.preview
response_variable: preview
```

The response includes:

- `groups_to_create`: missing Alexa rooms;
- `pending_additions`: endpoints waiting for room creation;
- `additions` and `removals`: membership changes;
- `ambiguous`: mappings intentionally blocked;
- `unmatched_alexa`: endpoints without an HA match;
- `change_count`: total planned changes.

Apply the plan:

```yaml
action: alexa_room_sync.apply
response_variable: result
```

Alexa Room Sync rereads both systems immediately before applying, creates
missing rooms, rereads the Alexa-assigned group IDs, and then performs
membership updates serially. It emits `alexa_room_sync_finished` when done.

### Cleaning stale Home Assistant skill endpoints

Alexa can retain devices previously exposed by the Home Assistant Alexa Smart
Home skill after their HA entities have been removed. Preview only endpoints
that are proven stale:

```yaml
action: alexa_room_sync.cleanup_preview
response_variable: cleanup
```

Deletion is never automatic. Copy only the desired `endpoint_id` values from
the preview and explicitly confirm the destructive action:

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
- This release does not rename or delete Alexa groups.
- Authentication and API behavior may differ between Amazon regions.

## Development

```bash
python -m compileall custom_components/alexa_room_sync
pytest -q
```

Pull requests are welcome. Please avoid including cookies, HAR files, email
addresses, endpoint IDs, or other account-specific data in tests and reports.

## License

[MIT](LICENSE)
