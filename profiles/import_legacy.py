"""Migrate the legacy data/profile.json firm into the firms table.

The legacy profile predates the controlled vocabulary and carries only what the
qualification engine needed, so several columns have to be inferred. Every inferred
value is recorded in ``import_notes`` as a ``TODO:`` line rather than being silently
presented as fact — read that column before trusting a firm's ranked list.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import config
from profiles import schema, vocabulary


LOGGER = logging.getLogger(__name__)

LEGACY_PROFILE_PATH = Path(config.PROJECT_ROOT) / "data" / "profile.json"

#: Legacy ``past_projects[].name`` text -> trade slug. The legacy file types every
#: project as "civil", which is too coarse to score against, so the work itself is
#: read from the project name.
PROJECT_TYPE_KEYWORDS = (
    ("culvert", "bridge_structural"),
    ("bridge", "bridge_structural"),
    ("pumphouse", "water_wastewater"),
    ("watermain", "water_wastewater"),
    ("sewer", "water_wastewater"),
    ("shoreline", "marine_shoreline"),
    ("dock", "marine_shoreline"),
    ("road", "roadwork"),
    ("paving", "roadwork"),
)

#: Values the legacy profile cannot supply. Each is applied with a TODO note.
INFERRED_REGIONS = [vocabulary.REGION_ONTARIO_ANY]
INFERRED_BUYER_TYPES = ["municipal", "federal"]
INFERRED_VALUE_MIN = 100_000.0
INFERRED_BIDS_PER_MONTH = 4


def load_legacy_profile(path: Path | str | None = None) -> dict:
    """Read the legacy profile JSON, returning an empty dict when unreadable."""
    source = Path(path) if path else LEGACY_PROFILE_PATH
    try:
        with source.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Could not read legacy profile %s: %s", source, exc)
        return {}
    return value if isinstance(value, dict) else {}


def build_firm(legacy: dict) -> dict:
    """Map a legacy profile onto a firms row, collecting TODO notes for gaps."""
    notes: list[str] = []

    name = str(legacy.get("firm_name") or "").strip() or "Unnamed firm"

    certifications = [str(item) for item in legacy.get("certifications") or []]
    # Ruling: keep "P.Eng on staff" as a certification rather than dropping it —
    # some tenders require professional-engineer involvement, so it stays checkable.
    for designation in legacy.get("staff_designations") or []:
        text = str(designation).strip()
        if text and text not in certifications:
            certifications.append(text)
            notes.append(
                f"staff_designations entry {text!r} was folded into certifications; "
                "the legacy schema had no separate column for it"
            )

    past_projects, project_notes = _past_projects(legacy)
    notes.extend(project_notes)

    trades = sorted(
        {
            str(project["type_slug"])
            for project in past_projects
            if project.get("type_slug")
        }
    )
    if trades:
        notes.append(
            "TODO: trades were inferred from past project names "
            f"({', '.join(trades)}); confirm the full trade list with the firm"
        )
    else:
        notes.append("TODO: no trades could be inferred; set them before ranking")

    bonding_single = _number(legacy.get("bonding_capacity_cad"))
    if bonding_single is not None:
        notes.append(
            "bonding_capacity_cad was read as bonding_single_project"
        )
        notes.append(
            "TODO: bonding_aggregate is unknown; the legacy profile has one "
            "bonding figure only"
        )

    insurance = legacy.get("insurance") or {}
    value_max = bonding_single
    if value_max is not None:
        notes.append(
            "TODO: value_max was set to the single-project bonding capacity "
            f"({value_max:,.0f}); confirm the firm's real upper limit"
        )
    notes.append(
        f"TODO: value_min was assumed to be {INFERRED_VALUE_MIN:,.0f}; "
        "the legacy profile states no floor"
    )

    legacy_regions = [str(item) for item in legacy.get("regions") or []]
    notes.append(
        f"TODO: regions {legacy_regions} were mapped to {INFERRED_REGIONS} "
        "(province-wide wildcard); narrow to counties/districts to sharpen "
        "region scoring"
    )
    notes.append(
        f"TODO: buyer_type_preferences {INFERRED_BUYER_TYPES} were inferred from "
        "past project buyers; confirm whether provincial, school board, or "
        "hospital work is wanted"
    )
    notes.append(
        f"TODO: bids_per_month_capacity was assumed to be {INFERRED_BIDS_PER_MONTH}"
    )

    return {
        "name": name,
        "trades": trades,
        "regions": list(INFERRED_REGIONS),
        "value_min": INFERRED_VALUE_MIN,
        "value_max": value_max,
        "bonding_single_project": bonding_single,
        "bonding_aggregate": None,
        "insurance_cgl": _number(insurance.get("cgl_limit")),
        "insurance_auto": _number(insurance.get("auto_limit")),
        "certifications": certifications,
        "submission_capabilities": _submission_capabilities(legacy, notes),
        "buyer_type_preferences": list(INFERRED_BUYER_TYPES),
        "bids_per_month_capacity": INFERRED_BIDS_PER_MONTH,
        "past_projects": past_projects,
        "import_notes": notes,
    }


def import_legacy(
    connection: Any = None,
    profile_path: Path | str | None = None,
    db_path: Any = None,
) -> dict:
    """Import the legacy profile and return the stored firm."""
    legacy = load_legacy_profile(profile_path)
    if not legacy:
        raise RuntimeError("The legacy profile could not be read; nothing was imported")

    firm = build_firm(legacy)
    owned = connection is None
    connection = connection or schema.connect(db_path)
    try:
        firm_id = schema.upsert_firm(connection, firm)
        stored = schema.get_firm(connection, firm_id)
    finally:
        if owned:
            connection.close()
    return stored or {}


def _past_projects(legacy: dict) -> tuple[list[dict], list[str]]:
    projects: list[dict] = []
    notes: list[str] = []
    for entry in legacy.get("past_projects") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        type_slug = _project_type_slug(name, entry.get("type"))
        if type_slug is None:
            notes.append(
                f"TODO: past project {name!r} could not be typed; set its type_slug"
            )
        projects.append(
            {
                "name": name,
                "type_slug": type_slug,
                "value": _number(entry.get("value_cad")),
                # Every legacy project is a county or township job.
                "buyer_type": "municipal",
                "year": entry.get("year"),
            }
        )
    if projects:
        notes.append(
            "TODO: past project buyer_type was assumed to be municipal for all "
            f"{len(projects)} legacy projects"
        )
    return projects, notes


def _project_type_slug(name: str, legacy_type: Any) -> str | None:
    text = f"{name} {legacy_type or ''}".casefold()
    for keyword, slug in PROJECT_TYPE_KEYWORDS:
        if keyword in text:
            return slug
    return None


def _submission_capabilities(legacy: dict, notes: list[str]) -> list[str]:
    capabilities: list[str] = []
    for item in legacy.get("submission_capabilities") or []:
        text = str(item).strip().casefold()
        if text in vocabulary.SUBMISSION_CAPABILITIES:
            capabilities.append(text)
        else:
            notes.append(
                f"TODO: submission capability {item!r} is outside the vocabulary "
                "and was dropped"
            )
    return capabilities


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Import data/profile.json into the firms table"
    )
    parser.add_argument("--profile", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    firm = import_legacy(profile_path=args.profile, db_path=args.db)
    print(f"firm {firm['id']}: {firm['name']}")
    for column in (
        "trades",
        "regions",
        "value_min",
        "value_max",
        "bonding_single_project",
        "insurance_cgl",
        "insurance_auto",
        "certifications",
        "submission_capabilities",
        "buyer_type_preferences",
        "bids_per_month_capacity",
    ):
        print(f"  {column:<24} {firm[column]}")
    print(f"  past_projects            {len(firm['past_projects'])}")
    print("\nimport notes:")
    for note in firm["import_notes"]:
        print(f"  - {note}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
