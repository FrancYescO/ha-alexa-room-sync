# Changelog

All notable changes to this project are documented in this file.

## 0.8.3

- Restricted automatic synchronization to configured endpoint models, Echo
  devices, Amazon-manufactured devices, and explicit manual mappings.
- Hardened stale-endpoint deletion so only unambiguous Home Assistant
  `entity_id` sources can be classified as obsolete.
- Preferred an entity ID extracted from the Home Assistant endpoint description
  over an opaque Alexa serial number.
- Fixed HACS validation checkout and pinned GitHub Actions to exact revisions.
- Added runtime and API regression coverage for endpoint filtering and cleanup.

## 0.8.2

- Echo endpoints with one exact, unique Home Assistant entity/area match now
  participate in normal room synchronization.
- Alexa clients without a safe HA match remain untouched and are reported in
  the device-room inventory.

## 0.8.1

- Added a full room-membership inventory for Amazon/Echo devices, including
  expected HA area, recognized Alexa room groups, and unrelated groups.
- Reports Amazon/Echo devices that are missing from their expected room or
  belong to multiple recognized rooms.
- Applies additions before removals so a failed Alexa request cannot leave an
  endpoint temporarily without its intended room.

## 0.8.0

- Added Alexa room-name auditing to every synchronization preview.
- Reports Echo/Alexa devices that belong to a room whose name differs from the
  corresponding Home Assistant area.
- Safely recognizes legacy Italian room names that differ only by articles or
  prepositions, such as `Camera da letto` and `Camera Letto`.
- Synchronization now removes mapped endpoints from these high-confidence
  legacy room aliases while leaving unrelated Alexa groups untouched.

## 0.7.0

- Added buttons to preview and delete stale Home Assistant Alexa endpoints.
- Added a two-step, 10-minute cleanup guard: deletion is limited to the exact
  candidates returned by the latest preview and every endpoint is revalidated.
- Added detailed cleanup preview and completion notifications.

## 0.6.0

- Added richer Alexa endpoint discovery, including Home Assistant skill origin,
  source entity IDs, descriptions, and legacy appliance identifiers.
- Added a read-only cleanup preview for Alexa endpoints whose HA entity no
  longer exists.
- Added guarded, explicit deletion of selected stale Home Assistant endpoints.

## 0.5.1

- Clarified that unmatched items are Alexa-side endpoints.
- Added Alexa endpoint ID, category, model, source serial/entity ID, and the
  reason a safe Home Assistant match could not be made to verification output.

## 0.5.0

- Added `button.alexa_room_sync_verify` to run a read-only verification.
- Added `button.alexa_room_sync_sync` to run the synchronization.
- Added persistent Home Assistant notifications for verification differences,
  synchronization results, and Alexa API errors.

## 0.4.0

- Added Alexa Media Player shared authentication.
- Added local Home Assistant brand icon and logo assets.
- Added HACS metadata and GitHub validation workflows.
- Expanded public installation, security, and usage documentation.

## 0.3.0

- Added stable MatterHub `entity_id` matching through Alexa serial numbers.
- Added automatic Alexa room creation.
- Expanded endpoint discovery beyond a single model filter.

## 0.1.0

- Initial preview and apply services using a captured Alexa HAR session.
