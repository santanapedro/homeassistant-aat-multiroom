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

# Manual section 1.3.8 (error codes) mapped to translation keys under the
# top-level "exceptions" section of strings.json/translations. Codes 17 and
# 18 mean slightly different things in different sections of the manual
# (invalid zone vs. invalid value, depending on the command), so we keep the
# wording generic enough to cover every command we actually send in v1
# (POWER/ZSTDBY/MUTE/VOL/INPSET).
ERROR_CODE_TRANSLATION_KEYS: dict[str, str] = {
    "7": "unknown_command",
    "8": "device_off",
    "17": "invalid_zone_or_value",
    "18": "value_out_of_range",
}
DEFAULT_ERROR_TRANSLATION_KEY = "command_failed"
