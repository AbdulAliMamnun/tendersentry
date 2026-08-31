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
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from census import schema as census_schema
from matchrec import schema as matchrec_schema
from profiles import schema as profiles_schema


LOGGER = logging.getLogger(__name__)

WEB_DATA_DIR = Path(config.PROJECT_ROOT) / "web" / "data"
TENDERS_DIR = Path(config.PROJECT_ROOT) / config.DATA_DIR

#: The curated blocker, committed so the export does not depend on `data/tenders/`.
#:
#: That directory is gitignored and has never existed on a CI runner, which is why the
#: daily job failed here every time: `select_blocker()` globs the filesystem, found
#: nothing, and the export refused to emit a board without a red row. The evidence now
#: travels with the repository instead of with one laptop.
#:
#: It carries the source PDF's sha256 rather than only the quote. On a product whose
#: whole claim is exact-substring verification, shipping the *result* of a verification
#: without any record of what was verified is the wrong artifact: the hash is what keeps
#: the claim checkable against the document it came from.
BLOCKER_PATH = WEB_DATA_DIR / "demo-blocker.json"

#: Bumped when the artifact's shape changes in a way an older reader would misread.
BLOCKER_ARTIFACT_VERSION = 1

#: Fields without which the red row cannot be rendered honestly.
REQUIRED_BLOCKER_FIELDS = (
    "tender_id",
    "title",
    "quote",
    "page",
    "source_file",
    "source_sha256",
    "extracted_at",
    "verified_at",
)

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


class BlockerArtifactMissing(RuntimeError):
    """Raised when the curated blocker is absent or unusable.

    Deliberately fatal, and deliberately not a fallback to a board with no red row.
    A blockerless board would be a legitimate state if we had checked and found no
    blocker; this is the different case where the evidence file is simply not there,
    which is a repository defect. Rendering the honest-looking outcome of a broken
    build would hide the break.
    """


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_blocker_artifact(
    connection: Any | None = None, preferred_check_value: str | None = "fax"
) -> dict:
    """Re-verify a blocker against its source PDF and return the artifact payload.

    **The verification is re-run here, against the PDF, not against `pages.json`.**
    ``extract_pages(force=True)`` re-reads the documents and ``find_quote`` is the same
    exact-substring matcher the extraction pipeline uses, so this artifact makes the
    pipeline's claim rather than a weaker restatement of it.

    Refuses on any of: no blocker, quote not found, or the quote found in a different
    file or on a different page than the requirement records. That last one matters —
    a quote that verifies somewhere else is a provenance failure, not a pass, and it is
    the case a looser check would wave through.
    """
    from extract import pages as extract_pages_module

    blocker = select_blocker(preferred_check_value)
    if blocker is None:
        raise BlockerArtifactMissing(
            f"No blocker with a verbatim quote under {TENDERS_DIR}. "
            "Run the extraction pipeline first."
        )

    tender_id = blocker["tender_id"]
    tender_dir = TENDERS_DIR / tender_id
    decision_path = tender_dir / "decision.json"

    requirement = _blocker_requirement(tender_id, blocker)
    source_file = str(requirement.get("source_file") or "")
    recorded_page = str(requirement.get("page_number") or "")
    pdf_path = tender_dir / "raw" / source_file
    if not pdf_path.is_file():
        raise BlockerArtifactMissing(
            f"The source PDF {pdf_path} is missing, so the quote cannot be re-verified. "
            "The artifact is only worth shipping if it was checked against the document."
        )

    LOGGER.info("Re-reading %s to verify the quote", pdf_path.name)
    pages_by_file = extract_pages_module.extract_pages(tender_id, force=True)
    found = extract_pages_module.find_quote(blocker["quote"], pages_by_file)
    if found is None:
        raise BlockerArtifactMissing(
            f"The quote for {tender_id} does not appear in {source_file}.\n"
            f"  quote: {blocker['quote'][:120]}\n"
            "Refusing to write an artifact from an unverified quote — that would make "
            "the file untrustworthy in exactly the way it exists to prevent."
        )
    found_file, found_page = found
    if found_file != source_file or str(found_page) != recorded_page:
        raise BlockerArtifactMissing(
            f"The quote for {tender_id} verifies at {found_file} p.{found_page}, but "
            f"the requirement records {source_file} p.{recorded_page}. A quote that "
            "verifies somewhere else is a provenance failure, not a pass."
        )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "artifact_version": BLOCKER_ARTIFACT_VERSION,
        "generated_at": now,
        "verified_at": now,
        # decision.json carries no internal timestamp, so this is the file's mtime.
        # A real weakness of the extraction pipeline, recorded in model/README.md.
        "extracted_at": datetime.fromtimestamp(
            decision_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
        "tender_id": tender_id,
        "title": blocker_title(tender_id, connection),
        "buyer": _blocker_buyer(tender_id, connection),
        "requirement_id": requirement.get("id"),
        "requirement_text": blocker["requirement_text"],
        "quote": blocker["quote"],
        "page": int(recorded_page or 0),
        "source_file": source_file,
        "source_sha256": file_sha256(pdf_path),
        "check_field": blocker.get("check_field"),
        "check_value": blocker.get("check_value"),
        "verification": "exact substring, re-read from the source PDF at generation",
    }


def _blocker_requirement(tender_id: str, blocker: dict) -> dict:
    """The requirement record behind a selected blocker."""
    path = TENDERS_DIR / tender_id / "requirements.json"
    for item in _read_json(path, []):
        if str(item.get("verbatim_quote") or "") == blocker["quote"]:
            return item
    raise BlockerArtifactMissing(f"No requirement in {path} carries the selected quote")


def _blocker_buyer(tender_id: str, connection: Any | None) -> str:
    if connection is None:
        return ""
    row = connection.execute(
        "SELECT buyer_name FROM tenders WHERE source_id = ? AND buyer_name IS NOT NULL",
        (str(tender_id),),
    ).fetchone()
    return str(row["buyer_name"]) if row and row["buyer_name"] else ""


def load_blocker(path: Path | str | None = None) -> dict:
    """Read the curated blocker, or fail naming --refresh-blocker."""
    artifact = Path(path or BLOCKER_PATH)
    if not artifact.is_file():
        raise BlockerArtifactMissing(
            f"No curated blocker at {artifact}.\n"
            "Regenerate it locally, where data/tenders/ exists:\n"
            "    python3 -m scripts.export_demo_board --refresh-blocker\n"
            "The board's red row must be real, verified, quoted evidence, and a "
            "missing artifact is a repository defect rather than a board with no "
            "blocker to show."
        )
    try:
        with artifact.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BlockerArtifactMissing(
            f"Could not read {artifact}: {exc}. Regenerate with --refresh-blocker."
        ) from exc

    version = payload.get("artifact_version")
    if version != BLOCKER_ARTIFACT_VERSION:
        raise BlockerArtifactMissing(
            f"{artifact} is version {version!r}; this code reads "
            f"{BLOCKER_ARTIFACT_VERSION}. Regenerate with --refresh-blocker."
        )
    absent = [field for field in REQUIRED_BLOCKER_FIELDS if not payload.get(field)]
    if absent:
        raise BlockerArtifactMissing(
            f"{artifact} is missing required field(s): {', '.join(absent)}. "
            "Regenerate with --refresh-blocker."
        )
    return payload


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

    # From the committed artifact, never from data/tenders/ — that directory is
    # gitignored and absent on every runner, which is the whole reason this step used
    # to fail daily.
    blocker = load_blocker()

    return {
        "firm": {"id": firm["id"], "name": firm["name"]},
        "rows": board_rows(connection, firm_id, rows),
        "blocker": {
            **blocker,
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


def refresh_blocker(db_path: Any = None, out: Path | str | None = None) -> Path:
    """Regenerate the curated blocker. Local only — needs data/tenders/ and pdfplumber."""
    destination = Path(out or BLOCKER_PATH)
    connection = matchrec_schema.connect(db_path)
    try:
        payload = build_blocker_artifact(connection)
    finally:
        connection.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_json(destination, payload)
    LOGGER.info(
        "Verified %s p.%s against %s (sha256 %s…) and wrote %s",
        payload["tender_id"],
        payload["page"],
        payload["source_file"],
        payload["source_sha256"][:12],
        destination,
    )
    return destination


def _main() -> None:
    parser = argparse.ArgumentParser(description="Export the demo board and stats")
    parser.add_argument("--firm", type=int, default=DEFAULT_FIRM_ID)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--out", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--refresh-blocker",
        action="store_true",
        help=(
            "re-verify the curated blocker against its source PDF and rewrite "
            "web/data/demo-blocker.json (local only; needs data/tenders/)"
        ),
    )
    args = parser.parse_args()

    try:
        if args.refresh_blocker:
            path = refresh_blocker(args.db)
            print(json.dumps(_read_json(path, {}), indent=2, ensure_ascii=False))
            return
        written = export(args.firm, args.rows, args.out, args.db)
    except BlockerArtifactMissing as error:
        raise SystemExit(str(error)) from error
    for name, path in written.items():
        print(f"wrote {name}: {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
