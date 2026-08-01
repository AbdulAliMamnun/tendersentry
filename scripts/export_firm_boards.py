"""Export one board file per firm, keyed by the hash of its board token.

**The filename is ``sha256(token)``, never the token.** These files are committed to
a public repository, and the token is the only thing standing between a stranger and
a firm's board — publishing it would hand out the key. The hash cannot be reversed,
so the repository holds nothing that opens a board.

What a board may contain is deliberately narrow. It carries the firm's display name,
its trades and regions, and its ranked opportunities. It carries no contact details,
no bonding or insurance figures, no internal identifiers, and nothing belonging to
any other firm. ``tests/test_export_firm_boards.py`` asserts those absences rather
than trusting this docstring.

Blocker evidence is attached only where the qualification engine has actually
produced it — a verbatim quote checked against the source page. Where it has not, the
section is omitted entirely. A board never says "no blockers found", because an
absence we have not verified is not a reassurance we are entitled to display.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import config
from census import schema as census_schema
from matchrec import schema as matchrec_schema
from notices import db
from profiles import schema as profiles_schema
from scripts.export_demo_board import blocker_reason


LOGGER = logging.getLogger(__name__)

BOARDS_DIR = Path(config.PROJECT_ROOT) / "web" / "data" / "boards"
TENDERS_DIR = Path(config.PROJECT_ROOT) / config.DATA_DIR

DEFAULT_ROWS = 25

#: Everything a board row may expose. Anything absent here never reaches the web.
ROW_FIELDS = (
    "rank",
    "title",
    "buyer",
    "closing_date",
    "score",
    "source",
    "flags",
)


def board_rows(
    connection: Any, firm_id: int, limit: int = DEFAULT_ROWS
) -> list[dict]:
    """The firm's ranked opportunities, trimmed to what a board may show."""
    rows = connection.execute(
        "SELECT t.source, t.source_id, t.title, t.buyer_name, t.closing_date_utc, "
        "       s.final_score, s.flags "
        "FROM firm_notice_scores s JOIN tenders t ON t.id = s.tender_id "
        "WHERE s.firm_id = ? "
        "ORDER BY s.final_score DESC, t.closing_date_utc, t.id LIMIT ?",
        (int(firm_id), int(limit)),
    ).fetchall()

    board: list[dict] = []
    for position, row in enumerate(rows, start=1):
        entry = {
            "rank": position,
            "title": str(row["title"]),
            "buyer": str(row["buyer_name"] or ""),
            "closing_date": str(row["closing_date_utc"] or "")[:10],
            "score": round(float(row["final_score"])),
            "source": str(row["source"]),
            "flags": matchrec_schema.loads(row["flags"], []),
        }
        blocker = verified_blocker(str(row["source_id"]))
        if blocker is not None:
            entry["blocker"] = blocker
        board.append(entry)
    return board


def verified_blocker(source_id: str) -> dict | None:
    """Blocker evidence for one notice, when the qualification engine has produced it.

    Returns None when no decision exists for the notice — the caller then omits the
    section rather than asserting the tender is clear.
    """
    decision_path = TENDERS_DIR / str(source_id) / "decision.json"
    if not decision_path.is_file():
        return None
    decision = _read_json(decision_path, {})
    if decision.get("verdict") != "no_bid" or not decision.get("blockers"):
        return None

    requirements = {
        str(item.get("id")): item
        for item in _read_json(decision_path.with_name("requirements.json"), [])
    }
    requirement = requirements.get(str(decision["blockers"][0]))
    if not requirement or not requirement.get("verbatim_quote"):
        return None
    return {
        "requirement_text": str(requirement.get("requirement_text", "")),
        "quote": str(requirement["verbatim_quote"]),
        "page": int(requirement.get("page_number") or 0),
        "check_value": requirement.get("check_value"),
    }


def build_board(connection: Any, firm: dict, rows: int = DEFAULT_ROWS) -> dict:
    """Assemble one firm's board payload."""
    board = board_rows(connection, int(firm["id"]), rows)
    for entry in board:
        if "blocker" in entry:
            entry["blocker"]["reason"] = blocker_reason(entry["blocker"], firm)

    return {
        "firm": {
            # Display identity only: no email, no bonding or insurance figures, no
            # internal id. The token in the URL is the sole identifier.
            "name": str(firm["name"]),
            "trades": [str(item) for item in firm.get("trades") or []],
            "regions": [str(item) for item in firm.get("regions") or []],
        },
        "generated_at": db.utc_timestamp(),
        "candidate_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM firm_notice_scores WHERE firm_id = ?",
                (int(firm["id"]),),
            ).fetchone()[0]
        ),
        "board": board,
    }


def export(
    out_dir: Path | str | None = None,
    rows: int = DEFAULT_ROWS,
    db_path: Any = None,
    firm_id: int | None = None,
) -> list[dict]:
    """Write a board file per tokenized firm and return what was written."""
    directory = Path(out_dir) if out_dir else BOARDS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    connection = matchrec_schema.connect(db_path)
    census_schema.create_schema(connection)
    written: list[dict] = []
    try:
        for firm in profiles_schema.list_firms(connection):
            token = str(firm.get("board_token") or "")
            if not token:
                LOGGER.warning(
                    "Firm %s has no board token; run python3 -m profiles.tokens "
                    "--backfill",
                    firm["id"],
                )
                continue
            if firm_id is not None and int(firm["id"]) != int(firm_id):
                continue

            payload = build_board(connection, firm, rows)
            key = profiles_schema.board_token_hash(token)
            destination = directory / f"{key}.json"
            with destination.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            written.append(
                {
                    "firm_id": int(firm["id"]),
                    "name": str(firm["name"]),
                    "rows": len(payload["board"]),
                    "blockers": sum(1 for row in payload["board"] if "blocker" in row),
                    "path": destination,
                }
            )
            LOGGER.info(
                "Exported %d row(s) for %s to %s",
                len(payload["board"]),
                firm["name"],
                destination.name,
            )
    finally:
        connection.close()
    return written


def _read_json(path: Path, default: Any) -> Any:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _main() -> None:
    parser = argparse.ArgumentParser(description="Export per-firm board JSON")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--firm", type=int, default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    written = export(args.out, args.rows, args.db, args.firm)
    for entry in written:
        print(
            f"firm {entry['firm_id']} ({entry['name']}): {entry['rows']} rows, "
            f"{entry['blockers']} with verified blockers → {entry['path'].name}"
        )
    if not written:
        print("no boards exported")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
