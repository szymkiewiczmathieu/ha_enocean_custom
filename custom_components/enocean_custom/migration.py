"""Config-entry option migrations, one per minor version.

Minor version 2 (v2.10.0). Since v2.7.0 the switch platform silently treated
every UI row with ``switch_type: RPS`` as a Ubiwizz UBID1507C
(``actuator_profile: ubiwizz_ubid1507c``) at runtime, while the documentation
promised that this profile is an operator choice that is never inferred. The
inference is gone. So that installations which relied on it keep exactly the
same behaviour and attributes, the formerly inferred profile is written into
those rows once, explicitly, where it can be seen and edited. Rows that
already carry a profile, non-RPS rows, and rows the profile would make invalid
are left untouched.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from .schema import UI_DEVICE_SCHEMA
from .ubiwizz import UBIWIZZ_UBID1507C_PROFILE


def explicit_rps_actuator_profiles(rows: Any) -> tuple[Any, int]:
    """Return the rows with the legacy RPS inference made explicit, and a count.

    Malformed rows (hand-edited ``.storage``) are carried over byte for byte:
    migration never repairs, drops or reorders them.
    """
    if not isinstance(rows, list):
        return rows, 0
    migrated: list[Any] = []
    changed = 0
    for raw in rows:
        explicit = _with_explicit_profile(raw)
        if explicit is None:
            migrated.append(raw)
        else:
            migrated.append(explicit)
            changed += 1
    return migrated, changed


def _with_explicit_profile(raw: Any) -> dict[str, Any] | None:
    """Return a copy of a legacy RPS switch row carrying the inferred profile."""
    if (
        not isinstance(raw, dict)
        or raw.get("platform") != "switch"
        or raw.get("switch_type") != "RPS"
        or raw.get("actuator_profile") is not None
    ):
        return None
    updated = {**raw, "actuator_profile": UBIWIZZ_UBID1507C_PROFILE}
    try:
        UI_DEVICE_SCHEMA(updated)
    except vol.Invalid:
        # The profile restricts the channel to 0/1; a row it would invalidate
        # never produced a working entity with the inference either, so it is
        # left as it is rather than being broken differently.
        return None
    return updated
