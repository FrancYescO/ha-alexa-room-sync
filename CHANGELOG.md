# Changelog

All notable changes to this project are documented in this file.

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
