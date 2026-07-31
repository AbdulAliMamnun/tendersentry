"""Export a firm's demo board and the site-wide stat strip to build-time JSON.

Read-only against the Python side: the website never runs the pipeline, it reads
what this script committed. Two sources are combined, because they answer different
questions:

* ``matchrec`` supplies the ranked rows — what the firm should look at, and why.
* ``match`` (the citation-verified qualification engine) supplies the blocker — the
  clause that disqualifies a bid, with the verbatim sentence and its true page.

Every number the site displays is produced here. Nothing is hardcoded in the page.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

import config
from census import schema as census_schema
from matchrec import schema as matchrec_schema
from profiles import schema as profiles_schema


LOGGER = logging.getLogger(__name__)

WEB_DATA_DIR = Path(config.PROJECT_ROOT) / "web" / "data"
TENDERS_DIR = Path(config.PROJECT_ROOT) / config.DATA_DIR

DEFAULT_FIRM_ID = 1
DEFAULT_ROWS = 2


def board_rows(
    connection: Any, firm_id: int, limit: int = DEFAULT_ROWS
) -> list[dict]:
    """Return the firm's highest-scoring notices for the board's green rows."""
    rows = connection.execute(
        "SELECT t.title, t.buyer_name, t.closing_date_utc, t.source, s.final_score "
        "FROM firm_notice_scores s JOIN tenders t ON t.id = s.tender_id "
        "WHERE s.firm_id = ? "
        "ORDER BY s.final_score DESC, t.closing_date_utc, t.id LIMIT ?",
        (int(firm_id), int(limit)),
    ).fetchall()
    return [
        {
            "title": str(row["title"]),
            "buyer": str(row["buyer_name"] or ""),
            "closing_date": str(row["closing_date_utc"] or "")[:10],
            "source": str(row["source"]),
            "score": round(float(row["final_score"])),
        }
        for row in rows
    ]


def find_blocker() -> dict | None:
    """Return one real blocker with its verbatim quote and verified page.

    Blockers come from the qualification engine's stored decisions, where every
    quote has already been checked character-for-character against the source PDF.
    """
    for decision_path in sorted(TENDERS_DIR.glob("*/decision.json")):
        decision = _read_json(decision_path, {})
        if decision.get("verdict") != "no_bid" or not decision.get("blockers"):
            continue
        requirements = {
            str(item.get("id")): item
            for item in _read_json(decision_path.with_name("requirements.json"), [])
        }
        requirement = requirements.get(str(decision["blockers"][0]))
        if not requirement or not requirement.get("verbatim_quote"):
            continue
        return {
            "tender_id": str(decision["tender_id"]),
            "requirement_text": str(requirement.get("requirement_text", "")),
            "quote": str(requirement["verbatim_quote"]),
            "page": int(requirement.get("page_number") or 0),
            "check_field": requirement.get("check_field"),
            "check_value": requirement.get("check_value"),
        }
    return None


def select_blocker(preferred_check_value: str | None = "fax") -> dict | None:
    """Prefer a blocker of a given kind, falling back to whatever exists."""
    blockers = []
    for decision_path in sorted(TENDERS_DIR.glob("*/decision.json")):
        decision = _read_json(decision_path, {})
        if decision.get("verdict") != "no_bid" or not decision.get("blockers"):
            continue
        requirements = {
            str(item.get("id")): item
            for item in _read_json(decision_path.with_name("requirements.json"), [])
        }
        for blocker_id in decision["blockers"]:
            requirement = requirements.get(str(blocker_id))
            if not requirement or not requirement.get("verbatim_quote"):
                continue
            blockers.append(
                {
                    "tender_id": str(decision["tender_id"]),
                    "requirement_text": str(requirement.get("requirement_text", "")),
                    "quote": str(requirement["verbatim_quote"]),
                    "page": int(requirement.get("page_number") or 0),
                    "check_field": requirement.get("check_field"),
                    "check_value": requirement.get("check_value"),
                }
            )
    if not blockers:
        return None
    if preferred_check_value:
        wanted = str(preferred_check_value).casefold()
        preferred = [
            item
            for item in blockers
            if wanted in str(item.get("check_value") or "").casefold()
            or wanted in item["quote"].casefold()
            or wanted in item["requirement_text"].casefold()
        ]
        if preferred:
            return preferred[0]
    return blockers[0]


def blocker_title(tender_id: str, connection: Any | None = None) -> str:
    """The heading for the blocker row: the tender, not the clause.

    Demo tenders were hand-placed and predate the notices table, so several carry no
    stored title. Rather than print an internal id, derive the solicitation number
    from the package filename — ``rfso-5p300-26-0001-a.pdf`` is "RFSO 5P300-26-0001",
    which is what the buyer calls it.
    """
    if connection is not None:
        row = connection.execute(
            "SELECT title FROM tenders WHERE source_id = ? AND title != ''",
            (str(tender_id),),
        ).fetchone()
        if row and row["title"]:
            return str(row["title"])

    raw_dir = TENDERS_DIR / str(tender_id) / "raw"
    if raw_dir.is_dir():
        for document in sorted(raw_dir.glob("*.pdf")):
            match = re.match(
                r"([a-z]{3,4})-([a-z0-9]+-\d+-\d+)", document.stem, re.IGNORECASE
            )
            if match:
                return f"{match.group(1).upper()} {match.group(2).upper()}"

    return str(tender_id).replace("-", " ").upper()


def blocker_reason(blocker: dict, firm: dict) -> str:
    """Plain-English verdict shown above the quote, as the app words it."""
    capabilities = [str(item) for item in firm.get("submission_capabilities") or []]
    electronic_only = bool(capabilities) and set(capabilities) <= {"email", "portal"}
    method = str(blocker.get("check_value") or "").strip().casefold()
    haystack = f"{blocker['quote']} {blocker['requirement_text']}".casefold()
    if "fax" in haystack or "facsimile" in haystack or method == "fax":
        requirement = "fax submission"
    elif "physical" in method or "deliver" in haystack:
        requirement = "physical delivery"
    else:
        requirement = "a submission method this firm does not support"
    tail = (
        "this firm submits electronically only"
        if electronic_only
        else f"this firm submits by {' and '.join(capabilities) or 'other means'} only"
    )
    return f"Requires {requirement} — {tail}."


def build_board(connection: Any, firm_id: int, rows: int = DEFAULT_ROWS) -> dict:
    """Assemble the demo board payload for one firm."""
    firm = profiles_schema.get_firm(connection, firm_id)
    if firm is None:
        raise ValueError(f"No firm with id {firm_id}")

    blocker = select_blocker()
    if blocker is None:
        raise RuntimeError(
            "No blocker with a verified quote is available; the board's red row "
            "must be real evidence, so refusing to emit a board without one"
        )

    return {
        "firm": {"id": firm["id"], "name": firm["name"]},
        "rows": board_rows(connection, firm_id, rows),
        "blocker": {
            **blocker,
            "title": blocker_title(blocker["tender_id"], connection),
            "reason": blocker_reason(blocker, firm),
        },
        "candidate_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM firm_notice_scores WHERE firm_id = ?",
                (int(firm_id),),
            ).fetchone()[0]
        ),
    }


def build_stats(connection: Any) -> dict:
    """Assemble the stat strip. Every figure is counted, never asserted."""
    notices = int(connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0])
    municipalities = int(
        connection.execute("SELECT COUNT(*) FROM municipalities").fetchone()[0]
    )
    verified = sum(
        len(_read_json(path, []))
        for path in sorted(TENDERS_DIR.glob("*/requirements.json"))
    )
    dropped = sum(
        len(_read_json(path, []))
        for path in sorted(TENDERS_DIR.glob("*/dropped.json"))
    )
    return {
        "notices_tracked": notices,
        "requirements_verified": verified,
        "fabrications_caught": dropped,
        "municipalities_mapped": municipalities,
    }


def export(
    firm_id: int = DEFAULT_FIRM_ID,
    rows: int = DEFAULT_ROWS,
    out_dir: Path | str | None = None,
    db_path: Any = None,
) -> dict[str, Path]:
    """Write demo-board.json and stats.json, returning the paths."""
    directory = Path(out_dir) if out_dir else WEB_DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    # The board needs firms and scores; the stat strip needs municipalities. Applying
    # both keeps the export working on a database where one stage has never run.
    connection = matchrec_schema.connect(db_path)
    census_schema.create_schema(connection)
    try:
        board = build_board(connection, firm_id, rows)
        stats = build_stats(connection)
    finally:
        connection.close()

    written = {
        "demo-board.json": _write_json(directory / "demo-board.json", board),
        "stats.json": _write_json(directory / "stats.json", stats),
    }
    LOGGER.info(
        "Exported board for firm %s (%d rows, blocker from %s) and stats %s",
        board["firm"]["name"],
        len(board["rows"]),
        board["blocker"]["tender_id"],
        stats,
    )
    return written


def _read_json(path: Path, default: Any) -> Any:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> Path:
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return Path(path)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Export the demo board and stats")
    parser.add_argument("--firm", type=int, default=DEFAULT_FIRM_ID)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--out", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    written = export(args.firm, args.rows, args.out, args.db)
    for name, path in written.items():
        print(f"wrote {name}: {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
