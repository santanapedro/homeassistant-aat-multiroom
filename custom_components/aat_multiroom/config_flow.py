"""Config flow for AAT Multiroom.

Step 1 (user): ask for host/port, probe the device (MODEL + GETALL) to find
out the model and how many zones it has.

Step 2 (naming): let the user name the equipment, every zone and every
audio input. Defaults are pre-filled ("Zona 1", "Entrada 1", ...).

The options flow repeats the naming step later on, pre-filled with the
current names, so the user can rename things from "Configure" without
removing and re-adding the integration.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import AatConnectionError, async_probe
from .const import (
    CONF_INPUT_NAMES,
    CONF_MODEL,
    CONF_ZONE_NAMES,
    DEFAULT_INPUT_COUNT,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    INPUT_COUNTS_BY_MODEL,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_model(model: str) -> str:
    return model.strip().upper().replace("-", "").replace(" ", "")


class AatMultiroomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AAT Multiroom."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._model: str = ""
        self._zone_count: int = 0
        self._input_count: int = DEFAULT_INPUT_COUNT

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            try:
                model, zone_count = await async_probe(host, port)
            except AatConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing %s:%s", host, port)
                errors["base"] = "unknown"
            else:
                self._host = host
                self._port = port
                self._model = _normalize_model(model)
                self._zone_count = zone_count
                self._input_count = INPUT_COUNTS_BY_MODEL.get(self._model, DEFAULT_INPUT_COUNT)
                return await self.async_step_naming()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_naming(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            name = user_input[CONF_NAME]
            zone_names = {
                str(i): user_input[f"zone_{i}"] for i in range(1, self._zone_count + 1)
            }
            input_names = {
                str(i): user_input[f"input_{i}"] for i in range(1, self._input_count + 1)
            }
            data = {
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_MODEL: self._model,
            }
            options = {
                CONF_ZONE_NAMES: zone_names,
                CONF_INPUT_NAMES: input_names,
            }
            return self.async_create_entry(title=name, data=data, options=options)

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=f"{DEFAULT_NAME} {self._model}".strip()): str,
        }
        for i in range(1, self._zone_count + 1):
            schema_dict[vol.Required(f"zone_{i}", default=f"Zona {i}")] = str
        for i in range(1, self._input_count + 1):
            schema_dict[vol.Required(f"input_{i}", default=f"Entrada {i}")] = str

        return self.async_show_form(
            step_id="naming",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "model": self._model,
                "zones": str(self._zone_count),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AatMultiroomOptionsFlow:
        return AatMultiroomOptionsFlow(config_entry)


class AatMultiroomOptionsFlow(config_entries.OptionsFlow):
    """Let the user rename the equipment's zones and inputs later on."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        zone_names: dict[str, str] = dict(self._entry.options.get(CONF_ZONE_NAMES, {}))
        input_names: dict[str, str] = dict(self._entry.options.get(CONF_INPUT_NAMES, {}))

        if user_input is not None:
            new_zone_names = {
                key[len("zone_") :]: value
                for key, value in user_input.items()
                if key.startswith("zone_")
            }
            new_input_names = {
                key[len("input_") :]: value
                for key, value in user_input.items()
                if key.startswith("input_")
            }
            return self.async_create_entry(
                title="",
                data={
                    CONF_ZONE_NAMES: new_zone_names,
                    CONF_INPUT_NAMES: new_input_names,
                },
            )

        schema_dict: dict[Any, Any] = {}
        for key in sorted(zone_names, key=int):
            schema_dict[vol.Required(f"zone_{key}", default=zone_names[key])] = str
        for key in sorted(input_names, key=int):
            schema_dict[vol.Required(f"input_{key}", default=input_names[key])] = str

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
