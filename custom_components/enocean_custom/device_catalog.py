"""Evidence-backed product commissioning catalog.

The catalog deliberately distinguishes a product recipe from a bare EEP.  A
recipe is selectable only when its evidence is explicit; unknown products stay
in the expert/manual path rather than being guessed from an EURID.

Two shapes live here. :class:`CommissioningProfile` is the original,
hand-written model used by the Ubiwizz guided pairing wizard (UTE channel
count, expected press count -- concepts specific to that one field-verified
product). :class:`ProductRecipe`, loaded from ``data/products.json``, is the
lighter "declare this profile from a product name instead of typing an EEP"
shortcut offered by the *declare profile* step: it pre-fills the exact same
``manual_eep`` field the step already validates, so a wrong or outdated
recipe can never do more damage than a wrong manual entry would. Per the
repository's own doctrine, a recipe is a convenience, never proof: every row
it creates still carries ``eep_source: manual``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final


@dataclass(frozen=True, slots=True)
class CommissioningProfile:
    """A product recipe used by the guided commissioning flow."""

    profile_id: str
    display_name: str
    eep: str
    channels: tuple[int, ...]
    manufacturer_id: int
    ute_number_of_channels: int
    press_count: int
    evidence: tuple[str, ...]


UBIWIZZ_LIGHT_D2_01_08: Final = CommissioningProfile(
    profile_id="ubiwizz_light_d2_01_08",
    display_name="Ubiwizz lighting module (field-verified)",
    eep="D2-01-08",
    channels=(0, 1),
    manufacturer_id=0x046,
    ute_number_of_channels=2,
    press_count=3,
    evidence=(
        "DolphinView capture supplied by operator",
        "field pairing procedure supplied by operator",
    ),
)

COMMISSIONING_PROFILES: Final = (UBIWIZZ_LIGHT_D2_01_08,)


def get_commissioning_profile(profile_id: object) -> CommissioningProfile | None:
    """Return a named recipe; never infer a product from an EEP or EURID."""
    if not isinstance(profile_id, str):
        return None
    return next((p for p in COMMISSIONING_PROFILES if p.profile_id == profile_id), None)


# Teach-in categories a ProductRecipe can name. "none" covers RPS rockers,
# which have no teach-in telegram at all (never confuse that with evidence).
TEACH_IN_KINDS: Final = ("none", "4bs", "ute")
_SUPPORT_VALUES: Final = ("supported", "manual", "unsupported")


@dataclass(frozen=True, slots=True)
class ProductRecipe:
    """One ``data/products.json`` entry: a named profile/platform shortcut."""

    recipe_id: str
    manufacturer: str
    model: str
    eep: str | None
    platforms: tuple[str, ...]
    channels: tuple[int, ...]
    teach_in: str
    support: str
    evidence: tuple[str, ...]
    notes: str | None = None

    @property
    def display_name(self) -> str:
        """Return the picker label: manufacturer and model, nothing invented."""
        return f"{self.manufacturer} {self.model}"


def _recipe_from_row(recipe_id: str, row: Any) -> ProductRecipe | None:
    """Validate one catalog row; a malformed entry vanishes rather than guesses."""
    try:
        manufacturer = row["manufacturer"]
        model = row["model"]
        eep = row.get("eep")
        raw_platforms = row.get("platforms", [])
        raw_channels = row.get("channels", [])
        teach_in = row["teach_in"]
        support = row["support"]
        raw_evidence = row.get("evidence", [])
        notes = row.get("notes")
    except (KeyError, TypeError):
        return None
    if not isinstance(manufacturer, str) or not manufacturer.strip():
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    if eep is not None and not isinstance(eep, str):
        return None
    if not isinstance(raw_platforms, list) or not all(
        isinstance(value, str) for value in raw_platforms
    ):
        return None
    if not isinstance(raw_channels, list) or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in raw_channels
    ):
        return None
    if teach_in not in TEACH_IN_KINDS or support not in _SUPPORT_VALUES:
        return None
    if not isinstance(raw_evidence, list) or not all(
        isinstance(value, str) for value in raw_evidence
    ):
        return None
    platforms = tuple(raw_platforms)
    channels = tuple(raw_channels)
    evidence = tuple(raw_evidence)
    if not evidence:
        return None
    if notes is not None and not isinstance(notes, str):
        return None
    return ProductRecipe(
        recipe_id=recipe_id,
        manufacturer=manufacturer,
        model=model,
        eep=eep,
        platforms=platforms,
        channels=channels,
        teach_in=teach_in,
        support=support,
        evidence=evidence,
        notes=notes,
    )


def _load_product_recipes() -> dict[str, ProductRecipe]:
    """Load the versioned local recipe file; runtime never uses the network."""
    path = Path(__file__).with_name("data") / "products.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw["recipes"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(rows, dict):
        return {}
    result: dict[str, ProductRecipe] = {}
    for recipe_id, row in rows.items():
        if not isinstance(recipe_id, str):
            continue
        recipe = _recipe_from_row(recipe_id, row)
        if recipe is not None:
            result[recipe_id] = recipe
    return result


PRODUCT_RECIPES: Final[MappingProxyType[str, ProductRecipe]] = MappingProxyType(
    _load_product_recipes()
)


def get_product_recipe(recipe_id: object) -> ProductRecipe | None:
    """Return one named recipe, or None; never infer a product from an EEP."""
    if not isinstance(recipe_id, str):
        return None
    return PRODUCT_RECIPES.get(recipe_id)


def recipes_for_platform(platform: object) -> tuple[ProductRecipe, ...]:
    """Return the selectable recipes for one row platform, sorted for display.

    Selectable means a decoder actually exists today: a declared EEP, the row's
    own platform among the recipe's platforms, and a support verdict other
    than ``unsupported``. Pending items (no platform wired up yet) are kept in
    the catalog for documentation but never offered in the picker.
    """
    if not isinstance(platform, str):
        return ()
    return tuple(
        sorted(
            (
                recipe
                for recipe in PRODUCT_RECIPES.values()
                if recipe.eep
                and recipe.support != "unsupported"
                and platform in recipe.platforms
            ),
            key=lambda recipe: recipe.display_name,
        )
    )
