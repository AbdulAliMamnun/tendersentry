"""Create and populate the firms table in the TenderSentry database."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from notices import db
from profiles import vocabulary


LOGGER = logging.getLogger(__name__)

#: Columns holding JSON-encoded arrays.
JSON_COLUMNS = (
    "trades",
    "regions",
    "certifications",
    "submission_capabilities",
    "buyer_type_preferences",
    "past_projects",
    "import_notes",
)

#: Everything a caller may set, in insert order.
FIRM_COLUMNS = (
    "name",
    "trades",
    "regions",
    "value_min",
    "value_max",
    "bonding_single_project",
    "bonding_aggregate",
    "insurance_cgl",
    "insurance_auto",
    "certifications",
    "submission_capabilities",
    "buyer_type_preferences",
    "bids_per_month_capacity",
    "past_projects",
    "import_notes",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS firms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        trades TEXT NOT NULL DEFAULT '[]',
        regions TEXT NOT NULL DEFAULT '[]',
        value_min REAL,
        value_max REAL,
        bonding_single_project REAL,
        bonding_aggregate REAL,
        insurance_cgl REAL,
        insurance_auto REAL,
        certifications TEXT NOT NULL DEFAULT '[]',
        submission_capabilities TEXT NOT NULL DEFAULT '[]',
        buyer_type_preferences TEXT NOT NULL DEFAULT '[]',
        bids_per_month_capacity INTEGER,
        past_projects TEXT NOT NULL DEFAULT '[]',
        import_notes TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)


def connect(db_path: Any = None) -> sqlite3.Connection:
    """Open the shared database with both the notices and firms schemas applied."""
    connection = db.connect(db_path)
    create_schema(connection)
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the firms table when it does not yet exist."""
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def validate(firm: dict) -> list[str]:
    """Return human-readable problems with a firm record, empty when valid."""
    problems: list[str] = []
    if not str(firm.get("name") or "").strip():
        problems.append("name is required")

    checks = (
        ("trades", vocabulary.TRADE_SLUGS),
        ("regions", vocabulary.REGION_SLUGS),
        ("submission_capabilities", vocabulary.SUBMISSION_CAPABILITIES),
        ("buyer_type_preferences", vocabulary.BUYER_TYPES),
    )
    for column, allowed in checks:
        values = firm.get(column) or []
        if not isinstance(values, list):
            problems.append(f"{column} must be a list")
            continue
        unknown = vocabulary.unknown_slugs(values, allowed)
        if unknown:
            problems.append(f"{column} has values outside the vocabulary: {unknown}")

    minimum = firm.get("value_min")
    maximum = firm.get("value_max")
    if minimum is not None and maximum is not None and float(minimum) > float(maximum):
        problems.append("value_min is greater than value_max")

    projects = firm.get("past_projects") or []
    if not isinstance(projects, list):
        problems.append("past_projects must be a list")
    else:
        for index, project in enumerate(projects):
            if not isinstance(project, dict):
                problems.append(f"past_projects[{index}] must be an object")
                continue
            type_slug = str(project.get("type_slug") or "")
            if type_slug and type_slug not in vocabulary.TRADE_SLUGS:
                problems.append(
                    f"past_projects[{index}].type_slug is outside the vocabulary: "
                    f"{type_slug}"
                )
    return problems


def upsert_firm(
    connection: sqlite3.Connection, firm: dict, now: str | None = None
) -> int:
    """Insert or update one firm by name and return its id."""
    problems = validate(firm)
    if problems:
        raise ValueError("Invalid firm profile: " + "; ".join(problems))

    timestamp = now or db.utc_timestamp()
    values = {column: _encode(column, firm.get(column)) for column in FIRM_COLUMNS}
    existing = connection.execute(
        "SELECT id FROM firms WHERE name = ?", (values["name"],)
    ).fetchone()

    with connection:
        if existing is None:
            cursor = connection.execute(
                f"INSERT INTO firms ({', '.join(FIRM_COLUMNS)}, created_at, updated_at) "
                "VALUES (" + ", ".join("?" for _ in FIRM_COLUMNS) + ", ?, ?)",
                [*(values[column] for column in FIRM_COLUMNS), timestamp, timestamp],
            )
            firm_id = int(cursor.lastrowid)
            LOGGER.info("Created firm %d: %s", firm_id, values["name"])
            return firm_id

        firm_id = int(existing["id"])
        connection.execute(
            "UPDATE firms SET "
            + ", ".join(f"{column} = ?" for column in FIRM_COLUMNS)
            + ", updated_at = ? WHERE id = ?",
            [*(values[column] for column in FIRM_COLUMNS), timestamp, firm_id],
        )
        LOGGER.info("Updated firm %d: %s", firm_id, values["name"])
        return firm_id


def get_firm(connection: sqlite3.Connection, firm_id: int) -> dict | None:
    """Load one firm with its JSON columns decoded."""
    row = connection.execute("SELECT * FROM firms WHERE id = ?", (firm_id,)).fetchone()
    return _decode_row(row) if row is not None else None


def list_firms(connection: sqlite3.Connection) -> list[dict]:
    """Load every firm with JSON columns decoded, ordered by id."""
    rows = connection.execute("SELECT * FROM firms ORDER BY id").fetchall()
    return [_decode_row(row) for row in rows]


def past_project_values(firm: dict) -> list[float]:
    """Return the usable numeric values of a firm's past projects."""
    values: list[float] = []
    for project in firm.get("past_projects") or []:
        if not isinstance(project, dict):
            continue
        value = project.get("value")
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            values.append(numeric)
    return values


def _encode(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS:
        return json.dumps(value if value is not None else [], ensure_ascii=False)
    if column == "name":
        return str(value or "").strip()
    if column == "bids_per_month_capacity":
        return None if value is None else int(value)
    if column in {
        "value_min",
        "value_max",
        "bonding_single_project",
        "bonding_aggregate",
        "insurance_cgl",
        "insurance_auto",
    }:
        return None if value is None else float(value)
    return value


def _decode_row(row: sqlite3.Row) -> dict:
    firm = dict(row)
    for column in JSON_COLUMNS:
        raw = firm.get(column)
        try:
            decoded = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            LOGGER.warning(
                "Firm %s has unreadable JSON in %s; treating it as empty",
                firm.get("id"),
                column,
            )
            decoded = []
        firm[column] = decoded if isinstance(decoded, list) else []
    return firm
