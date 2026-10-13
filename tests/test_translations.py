"""Guards on strings.json and its translations.

Home Assistant resolves a HomeAssistantError's `translation_key` to
`exceptions.<key>.message` - so an entry written as a bare string instead
of `{"message": ...}` fails hassfest *and* silently loses the translated
message at runtime. That exact mistake shipped once (v0.12.0) and was only
caught by CI, hence this test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.aat_multiroom.const import (
    DEFAULT_ERROR_TRANSLATION_KEY,
    ERROR_CODE_TRANSLATION_KEYS,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "aat_multiroom"
FILES = ["strings.json", "translations/en.json", "translations/pt-BR.json"]


def load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def key_paths(data: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        if isinstance(value, dict):
            paths |= key_paths(value, path)
    return paths


@pytest.mark.parametrize("name", FILES)
def test_every_exception_is_a_message_mapping(name: str) -> None:
    for key, value in load(name)["exceptions"].items():
        assert isinstance(value, dict), f"{name}: exceptions.{key} must be {{'message': ...}}"
        assert isinstance(value.get("message"), str) and value["message"], f"{name}: {key}"


def test_all_translation_files_have_the_same_keys() -> None:
    reference = key_paths(load("strings.json"))
    for name in FILES[1:]:
        assert key_paths(load(name)) == reference, name


def test_every_translation_key_used_in_code_exists() -> None:
    exceptions = load("strings.json")["exceptions"]
    used = set(ERROR_CODE_TRANSLATION_KEYS.values()) | {DEFAULT_ERROR_TRANSLATION_KEY}
    # Keys passed straight to HomeAssistantError/ServiceValidationError.
    for source in COMPONENT.glob("*.py"):
        used |= set(re.findall(r'translation_key="([a-z_]+)"', source.read_text(encoding="utf-8")))
    missing = used - set(exceptions)
    assert not missing, f"translation keys used in code but missing from strings.json: {missing}"


def test_the_group_command_service_is_documented_in_every_language() -> None:
    for name in FILES:
        service = load(name)["services"]["group_command"]
        assert {"name", "description", "fields"} <= set(service)
        assert {"action", "group", "value", "config_entry_id"} <= set(service["fields"])


def test_manifest_has_the_keys_hacs_requires() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    for key in ("domain", "name", "documentation", "issue_tracker", "version", "codeowners"):
        assert key in manifest, key
    assert list(manifest)[:2] == ["domain", "name"]
    assert list(manifest)[2:] == sorted(list(manifest)[2:]), "manifest keys must be sorted"
