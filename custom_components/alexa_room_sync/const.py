"""Constants for Alexa Room Sync."""

DOMAIN = "alexa_room_sync"

PLATFORMS = ["button", "select", "sensor"]

AUTH_ALEXA_DEVICES = "alexa_devices"
CONF_ALEXA_DEVICES_ENTRY_ID = "alexa_devices_entry_id"
DEFAULT_OPTIONS = {
    "dry_run": True,
    "allow_writes": False,
    "auto_sync": False,
    "include_echos": True,
    "floor_groups": False,
    "floor_lights_only": True,
    "include_climate": True,
    "include_child_locks": False,
    "cloud_devices": False,
    "resend_missing": False,
    "group_prefix": "",
    "excluded_areas": [],
}

CONF_COOKIE = "cookie"
CONF_AUTH_METHOD = "auth_method"
CONF_ALEXA_MEDIA_ENTRY_ID = "alexa_media_entry_id"
CONF_ENDPOINT_MODELS_JSON = "endpoint_models_json"
CONF_HEADERS_JSON = "headers_json"
CONF_HOST = "host"
CONF_MANUAL_MAPPINGS_JSON = "manual_mappings_json"

DEFAULT_HOST = "https://eu-api-alexa.amazon.it"
DEFAULT_ENDPOINT_MODELS_JSON = '["MatterHub"]'

AUTH_ALEXA_MEDIA = "alexa_media"
AUTH_COOKIE = "cookie"
ALEXA_MEDIA_DOMAIN = "alexa_media"

SERVICE_PREVIEW = "preview"
SERVICE_APPLY = "apply"
SERVICE_CAPABILITIES = "capabilities"
SERVICE_CLEANUP_PREVIEW = "cleanup_preview"
SERVICE_DELETE_STALE_ENDPOINTS = "delete_stale_endpoints"

EVENT_SYNC_FINISHED = "alexa_room_sync_finished"

UPDATE_ADD = "ADD"
UPDATE_REMOVE = "REMOVE"
