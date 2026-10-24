"""The ``device`` config subentry flow: add or reconfigure one radio device.

Home Assistant shows an *Add device* button on the integration page for every
subentry type an integration declares, plus *Reconfigure* on each existing
subentry. This flow is the radio-silent half of device management: it takes
an EnOcean ID (typed, or read from an Alliance commissioning label) and the
same platform/details forms the guided options flow uses, and creates one
subentry holding one row. Everything that transmits -- teach-in windows, UTE
association, guided D2 pairing -- stays in the options flow, which writes the
very same rows; the update listener in ``__init__`` turns them into
subentries too.

Reconfiguring renames the device (its first row carries the device name) and
can declare the EEP of a row whose profile is still unknown, with the exact
identity-preservation rules of the options flow's *Declare profile* step.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import CONF_NAME, CONF_PLATFORM
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import UI_DEVICE_PLATFORMS
from .device_catalog import get_product_recipe, recipes_for_platform
from .device_form import (
    CONF_MANUAL_EEP,
    CONF_QR_CODE,
    declare_profile_on_row,
    declared_generic_eep,
    device_details_schema,
    identity_collisions,
    manual_eep_offered,
    validate_device_details,
)
from .device_intelligence import (
    apply_manual_eep,
    flow_placeholders,
    metadata_declares_eep,
    parse_commissioning_identity,
    parse_manual_eep,
    recommended_platform,
    reconcile_commissioning_metadata,
)
from .device_rows import (
    SUBENTRY_ROWS,
    configured_rows,
    sender_identifier,
    subentries_by_sender,
    subentry_rows,
)
from .enocean_library.utils import combine_hex, to_hex_string
from .entity import get_gateway_base_id
from .learn import get_learn_manager
from .runtime import get_gateway
from .schema import used_sender_ids
from .sender_pool import allocate_sender_id

CONF_ROW = "row"
CONF_CATALOG_PRODUCT = "catalog_product"
CATALOG_CUSTOM = "__custom__"


def _user_schema(
    radio_metadata: dict[str, Any] | None,
    *,
    name: str | None = None,
    code: str | None = None,
    platform: str | None = None,
    manual_eep: str = "",
) -> vol.Schema:
    """Return the identify-and-name form, pre-filled after a rejected submit."""
    recommendation = platform or recommended_platform(radio_metadata)
    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME, **({"default": name} if name else {})): (
            selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        ),
        vol.Required(CONF_QR_CODE, **({"default": code} if code else {})): (
            selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )
        ),
        vol.Required(
            CONF_PLATFORM, **({"default": recommendation} if recommendation else {})
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(UI_DEVICE_PLATFORMS))
        ),
        vol.Optional(CONF_MANUAL_EEP, default=manual_eep): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }
    return vol.Schema(schema)


def _reconfigure_schema(
    title: str, undeclared: dict[str, str], recipes: tuple[Any, ...]
) -> vol.Schema:
    """Return the rename form, plus a profile declaration when a row needs one."""
    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=title): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        )
    }
    if undeclared:
        schema[vol.Required(CONF_ROW, default=next(iter(undeclared)))] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": value, "label": label}
                        for value, label in undeclared.items()
                    ]
                )
            )
        )
        if recipes:
            schema[vol.Required(CONF_CATALOG_PRODUCT, default=CATALOG_CUSTOM)] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": CATALOG_CUSTOM, "label": "Custom (type the EEP)"},
                            *(
                                {
                                    "value": recipe.recipe_id,
                                    "label": recipe.display_name,
                                }
                                for recipe in recipes
                            ),
                        ]
                    )
                )
            )
        schema[vol.Optional(CONF_MANUAL_EEP, default="")] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        )
    return vol.Schema(schema)


class EnOceanDeviceSubentryFlow(ConfigSubentryFlow):
    """Add one radio device from its ID, or reconfigure an existing one."""

    def __init__(self) -> None:
        """Start with nothing captured."""
        self._captured_id: list[int] | None = None
        self._radio_metadata: dict[str, Any] | None = None
        self._name: str | None = None
        self._platform: str | None = None

    # -- add -----------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Identify the device by its commissioning label or typed ID and name it."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_user_schema(None))
        name = str(user_input.get(CONF_NAME) or "").strip()
        code = user_input.get(CONF_QR_CODE)
        platform = user_input.get(CONF_PLATFORM)
        manual = user_input.get(CONF_MANUAL_EEP)
        manual_text = manual if isinstance(manual, str) else ""

        def show(errors: dict[str, str]) -> SubentryFlowResult:
            return self.async_show_form(
                step_id="user",
                data_schema=_user_schema(
                    self._radio_metadata,
                    name=name or None,
                    code=code if isinstance(code, str) else None,
                    platform=platform if platform in UI_DEVICE_PLATFORMS else None,
                    manual_eep=manual_text,
                ),
                errors=errors,
            )

        parsed = parse_commissioning_identity(code)
        if parsed is None:
            return show({CONF_QR_CODE: "invalid_qr_code"})
        if not name:
            return show({CONF_NAME: "name_required"})
        if platform not in UI_DEVICE_PLATFORMS:
            return show({CONF_PLATFORM: "invalid_device_details"})
        captured_id = list(parsed.sender_id)
        entry = self._get_entry()
        if combine_hex(captured_id) in subentries_by_sender(entry):
            return self.async_abort(reason="already_configured")
        metadata = reconcile_commissioning_metadata(
            parsed, get_learn_manager(self.hass).captured_metadata
        )
        # A profile the label already declares is never reconsidered; a typed
        # one is recorded as an operator assertion, exactly as in the options
        # flow's device form.
        if manual_text.strip() and manual_eep_offered(metadata):
            manual_eep = parse_manual_eep(manual_text)
            if manual_eep is None:
                return show({CONF_MANUAL_EEP: "invalid_eep"})
            metadata = apply_manual_eep(metadata, manual_eep)
        self._captured_id = captured_id
        self._radio_metadata = metadata
        self._name = name
        self._platform = platform
        return await self.async_step_details()

    def _suggested_sender_id(self) -> str:
        """Return a free Base ID + offset as a default, or "" (see options flow)."""
        base_id = get_gateway_base_id(get_gateway(self.hass))
        used = used_sender_ids(configured_rows(self._get_entry()))
        allocated = allocate_sender_id(base_id, used)
        return to_hex_string(allocated) if allocated is not None else ""

    def _show_details(self, errors: dict[str, str] | None = None) -> SubentryFlowResult:
        assert self._platform is not None
        return self.async_show_form(
            step_id="details",
            data_schema=device_details_schema(
                self._platform,
                suggested_sender_id=self._suggested_sender_id(),
                declared_eep=declared_generic_eep(self._radio_metadata),
            ),
            description_placeholders={
                "captured_id": to_hex_string(self._captured_id),
                "name": self._name or "",
                "platform": self._platform,
                **flow_placeholders(self._radio_metadata),
            },
            errors=errors or {},
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect the platform-specific fields and create the subentry."""
        if self._captured_id is None or self._name is None or self._platform is None:
            return self.async_abort(reason="device_form_missing")
        if user_input is None:
            return self._show_details()
        entry = self._get_entry()
        rows = configured_rows(entry)
        outcome = validate_device_details(
            self._platform,
            user_input,
            captured_id=self._captured_id,
            name=self._name,
            radio_metadata=self._radio_metadata,
            used_sender_ids=used_sender_ids(rows),
        )
        if outcome.row is None:
            return self._show_details(outcome.errors)
        self._radio_metadata = outcome.radio_metadata
        if combine_hex(self._captured_id) in subentries_by_sender(entry):
            return self.async_abort(reason="already_configured")
        if identity_collisions(
            outcome.row, rows, er.async_get(self.hass).async_get_entity_id
        ):
            return self._show_details({"base": "unique_id_exists"})
        return self.async_create_entry(
            title=self._name,
            data={SUBENTRY_ROWS: [outcome.row]},
            unique_id=sender_identifier(self._captured_id),
        )

    # -- reconfigure ----------------------------------------------------------

    @callback
    def _undeclared_rows(self, rows: list[dict]) -> dict[str, str]:
        """Index the rows whose profile is unknown or only asserted, as choices."""
        return {
            str(
                index
            ): f"{row['name']} ({row['platform']}, channel {row.get('channel', 0)})"
            for index, row in enumerate(rows)
            if not metadata_declares_eep(row.get("radio_metadata"))
        }

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename the device and, when a row has no profile yet, declare one."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        rows = subentry_rows(subentry)
        if not rows:
            return self.async_abort(reason="device_form_missing")
        undeclared = self._undeclared_rows(rows)
        recipes = ()
        if undeclared:
            first = rows[int(next(iter(undeclared)))]
            recipes = recipes_for_platform(first["platform"])
        placeholders = {
            "name": subentry.title,
            "captured_id": to_hex_string(rows[0]["id"]),
            "rows": str(len(rows)),
        }

        def show(errors: dict[str, str]) -> SubentryFlowResult:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_reconfigure_schema(subentry.title, undeclared, recipes),
                description_placeholders=placeholders,
                errors=errors,
            )

        if user_input is None:
            return show({})
        name = str(user_input.get(CONF_NAME) or "").strip()
        if not name:
            return show({CONF_NAME: "name_required"})
        new_rows = [dict(row) for row in rows]
        if name != new_rows[0]["name"]:
            new_rows[0]["name"] = name

        chosen_recipe = user_input.get(CONF_CATALOG_PRODUCT)
        submitted = user_input.get(CONF_MANUAL_EEP)
        if chosen_recipe and chosen_recipe != CATALOG_CUSTOM:
            recipe = get_product_recipe(chosen_recipe)
            submitted = recipe.eep if recipe else None
        if undeclared and isinstance(submitted, str) and submitted.strip():
            try:
                index = int(user_input.get(CONF_ROW, next(iter(undeclared))))
            except (TypeError, ValueError):
                return show({CONF_ROW: "invalid_device_details"})
            if str(index) not in undeclared:
                return show({CONF_ROW: "invalid_device_details"})
            eep = parse_manual_eep(submitted)
            if eep is None:
                return show({CONF_MANUAL_EEP: "invalid_eep"})
            other_rows = [
                row
                for row in configured_rows(entry)
                if combine_hex(row["id"]) != combine_hex(rows[0]["id"])
            ] + [row for position, row in enumerate(new_rows) if position != index]
            updated_raw, errors = declare_profile_on_row(
                new_rows[index],
                rows[index],
                eep,
                other_rows=other_rows,
                registry_lookup=er.async_get(self.hass).async_get_entity_id,
            )
            if updated_raw is None:
                return show(errors)
            new_rows[index] = updated_raw
        return self.async_update_and_abort(
            entry,
            subentry,
            title=name,
            data={**subentry.data, SUBENTRY_ROWS: new_rows},
        )
