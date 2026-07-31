"""Controlled vocabularies shared by firm profiles and the matching engine.

This module is the single source of truth for the slugs stored in the ``firms``
table and produced by ``matchrec.trades``. ``matchrec`` validates its mapping file
against ``TRADE_SLUGS`` at load time, so a typo in the mapping JSON fails loudly
instead of silently creating a slug nothing can match.
"""

from __future__ import annotations


#: Trade and work-type vocabulary. Order is meaningful only for display.
TRADE_SLUGS: tuple[str, ...] = (
    "roadwork",
    "sitework",
    "granular_supply",
    "bridge_structural",
    "concrete_flatwork",
    "water_wastewater",
    "utilities_underground",
    "building_general",
    "building_envelope",
    "electrical",
    "mechanical_hvac",
    "demolition_abatement",
    "landscaping",
    "marine_shoreline",
    "fencing_guiderail",
    "snow_ice_management",
    "environmental_remediation",
    "engineering_survey",
    "equipment_rental",
    "facility_maintenance",
)

#: Statuses a notice can carry instead of a trade slug.
TRADE_STATUS_UNMAPPED = "unmapped"
TRADE_STATUS_NON_CONSTRUCTION = "non_construction"
TRADE_STATUS_MAPPED = "mapped"
TRADE_STATUSES: tuple[str, ...] = (
    TRADE_STATUS_MAPPED,
    TRADE_STATUS_UNMAPPED,
    TRADE_STATUS_NON_CONSTRUCTION,
)

#: Ontario upper-tier counties, regions, districts, and single-tier cities.
ONTARIO_REGIONS: tuple[str, ...] = (
    "algoma",
    "brant",
    "bruce",
    "chatham_kent",
    "cochrane",
    "dufferin",
    "durham",
    "elgin",
    "essex",
    "frontenac",
    "greater_sudbury",
    "grey",
    "haldimand_norfolk",
    "haliburton",
    "halton",
    "hamilton",
    "hastings",
    "huron",
    "kawartha_lakes",
    "kenora",
    "lambton",
    "lanark",
    "leeds_grenville",
    "lennox_addington",
    "manitoulin",
    "middlesex",
    "muskoka",
    "niagara",
    "nipissing",
    "northumberland",
    "ottawa",
    "oxford",
    "parry_sound",
    "peel",
    "perth",
    "peterborough",
    "prescott_russell",
    "prince_edward",
    "rainy_river",
    "renfrew",
    "simcoe",
    "stormont_dundas_glengarry",
    "sudbury_district",
    "thunder_bay",
    "timiskaming",
    "toronto",
    "waterloo",
    "wellington",
    "york",
)

#: Wildcards for firms that do not work at county granularity.
REGION_ONTARIO_ANY = "ontario_any"
REGION_QUEBEC = "quebec"
REGION_FEDERAL_ANY = "federal_any"

REGION_SLUGS: tuple[str, ...] = (
    *ONTARIO_REGIONS,
    REGION_ONTARIO_ANY,
    REGION_QUEBEC,
    REGION_FEDERAL_ANY,
)

#: Province code each region slug resolves to. Notices are stored at province
#: granularity by ``notices``, so this is how a county-level firm profile is
#: compared against them at all.
REGION_PROVINCES: dict[str, str] = {
    **{slug: "ON" for slug in ONTARIO_REGIONS},
    REGION_ONTARIO_ANY: "ON",
    REGION_QUEBEC: "QC",
}

#: Buyer types a firm can express a preference for.
BUYER_TYPES: tuple[str, ...] = (
    "municipal",
    "provincial",
    "federal",
    "school_board",
    "hospital",
    "other",
)

#: ``notices`` infers its own buyer types; these are the same concepts under
#: different names, so notice values are translated before comparison.
BUYER_TYPE_ALIASES: dict[str, str] = {
    "health": "hospital",
    "education": "school_board",
}

SUBMISSION_CAPABILITIES: tuple[str, ...] = ("email", "portal", "physical")


def normalize_buyer_type(value: str | None) -> str | None:
    """Translate a notice's buyer type onto the firm-preference vocabulary."""
    text = str(value or "").strip().casefold()
    if not text or text == "unknown":
        return None
    translated = BUYER_TYPE_ALIASES.get(text, text)
    return translated if translated in BUYER_TYPES else "other"


def provinces_for_regions(regions: list[str] | tuple[str, ...]) -> set[str]:
    """Return the province codes a firm's region list covers."""
    return {
        REGION_PROVINCES[slug]
        for slug in regions
        if slug in REGION_PROVINCES
    }


def unknown_slugs(values: list, allowed: tuple[str, ...]) -> list[str]:
    """Return the supplied values that are outside a controlled vocabulary."""
    return [str(value) for value in values if str(value) not in allowed]
