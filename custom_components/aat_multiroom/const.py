"""Constants for the AAT Multiroom integration."""

DOMAIN = "aat_multiroom"

DEFAULT_PORT = 5000
DEFAULT_NAME = "AAT Multiroom"
DEFAULT_INPUT_COUNT = 8

MAX_VOLUME = 87

# How often we re-sync the full state as a safety net, on top of the
# push-based updates that arrive as soon as anything changes.
REFRESH_INTERVAL = 30

CONF_MODEL = "model"
CONF_ZONE_NAMES = "zone_names"
CONF_INPUT_NAMES = "input_names"

# Number of physical audio inputs per model, taken from the cover page of the
# AAT API manual (Rev. 12). Used only as a sane default during setup; the
# user can still rename/ignore inputs afterwards. Unknown models fall back
# to DEFAULT_INPUT_COUNT.
INPUT_COUNTS_BY_MODEL: dict[str, int] = {
    "PMA1": 4,
    "PMA2": 4,
    "PMRH2": 6,
    "PMRH4": 6,
    "PMRH6": 6,
    "PMR4": 4,
    "PMR5": 4,
    "PMR6": 6,
    "PMR7": 6,
    "PMR8": 5,
    "PMR9": 6,
    "PMR10": 6,
    "PMR11": 6,
    "PMR12": 6,
    "PMR13": 5,
}
