"""Constants for the AAT Multiroom integration."""

DOMAIN = "aat_multiroom"

DEFAULT_PORT = 5000
DEFAULT_NAME = "AAT Multiroom"
DEFAULT_INPUT_COUNT = 8

MAX_VOLUME = 87

# Raw device ranges for the per-zone audio adjustments (manual sections 1.8,
# 1.9 and 1.11). The entities translate these to dB for display; the device
# itself only ever sees the raw values below.
#   BASSSET/TREBLESET: 0..14 - 0 = -14 dB, 7 = flat, 14 = +14 dB (2 dB steps)
#   BALSET:            0..20 - 0 = full left, 10 = centered, 20 = full right
#   PREAMPSET:         0..7  - 0 = 0 dB, 7 = +14 dB (2 dB steps)
TONE_MAX = 14
TONE_FLAT = 7
BALANCE_MAX = 20
BALANCE_CENTER = 10
PREAMP_MAX = 7

# VOLLIMIT (manual 1.20.1.2, firmware >= 3.08): per-zone volume ceiling.
# 44..87, where 87 means "no limit".
VOLUME_LIMIT_MIN = 44

# Group / party commands (manual 1.20.2, firmware >= 3.08) are UDP
# broadcasts to this fixed port and are never answered. Group 0 is party
# mode; 1..32 are user-defined groups.
GROUP_UDP_PORT = 5000
GROUP_MAX = 32

# Zone output mode, set with SWSTEREO / SWMONO / SWBRIDGE. Write-only on the
# device (no GET, and not part of GETALL).
OUTPUT_MODES: dict[str, str] = {
    "stereo": "SWSTEREO",
    "mono": "SWMONO",
    "bridge": "SWBRIDGE",
}

# Models with no mono/stereo/bridge hardware: the PMRH 2/4/6 and the
# non-amplified PMA 1/2 (manual 1.10.1.1) - commands are accepted and do
# nothing there.
MODELS_WITHOUT_OUTPUT_MODE_PREFIXES = ("PMRH", "PMA")

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
