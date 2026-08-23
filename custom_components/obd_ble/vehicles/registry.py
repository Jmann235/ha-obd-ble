"""Registry of available vehicle profiles."""

from __future__ import annotations

from .base import VehicleProfile
from .chevy_bolt import CHEVY_BOLT_2017_2018, CHEVY_BOLT_2019_PLUS
from .kia_ceed_phev import KIA_CEED_PHEV
from .kia_ev6 import KIA_EV6

PROFILES: dict[str, VehicleProfile] = {
    profile.key: profile
    for profile in (
        CHEVY_BOLT_2017_2018,
        CHEVY_BOLT_2019_PLUS,
        KIA_CEED_PHEV,
        KIA_EV6,
    )
}


def get_profile(key: str) -> VehicleProfile:
    try:
        return PROFILES[key]
    except KeyError as err:
        raise ValueError(f"unknown vehicle profile: {key!r}") from err
