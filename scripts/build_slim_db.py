"""Project the full database down to what the daily refresh actually reads.

The daily path — ingest, re-map, scale backfill, re-rank, export — reads only
``tenders`` and ``notice_trades``, plus ``firms`` and a count off ``municipalities``.
Milestone 13 removed the last two consumers of ``bid_interactions`` and
``firm_entities`` by making both models artifacts, so those two tables — 356 MB of a
419 MB database — are no longer touched by a daily run at all.

Measured on the current database: **419 MB → 35.1 MB** at extraction, settling to
58.4 MB once the first re-rank writes its exclusion rows back.

**Three phases, and the third is not optional.** ``VACUUM INTO`` copies the whole
database, so dropping a table afterwards frees pages inside the copy without shrinking
the file. A second, plain ``VACUUM`` is what actually reclaims them. One pass leaves a
419 MB file with 384 MB of free pages in it.

**The slim database is disposable, not authoritative.** It is a projection, regenerated
after every retrain, and nothing in it is a source of truth that the full database does
not already hold. Two consequences are designed in rather than left to discipline:

* Every slim database carries a ``slim_provenance`` row naming the database it came
  from, when, and — through ``booster_sha256`` and the scale estimator's timestamp —
  which retrain generation it descends from. The daily job prints that row into its job
  summary, so "which retrain is production running against" is answerable at a glance
  instead of by inference.
* Projecting a projection is refused. A source that already carries
  ``slim_provenance`` is a slim database, and re-slimming it would produce a stamp that
  points at a copy rather than at the retrain.

**Retraining from a slim database is structurally impossible, which is the point.**
``model.scale --fit`` and ``export_model_service --refit`` both read
``bid_interactions``, and it is not here. That is what keeps the daily job incapable of
producing a ranking model the published evaluation no longer describes — enforced by
the absence of the data, not by a flag anyone could set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


LOGGER = logging.getLogger(__name__)

DEFAULT_DEST = Path(config.PROJECT_ROOT) / "data" / "tendersentry-slim.db"

#: Tables the daily path reads or writes. Everything else is dropped.
#:
#: `municipalities` earns its place on one COUNT(*) in export_demo_board; `firms` and
#: `notice_documents` are small and read by the board exporters. The two matchrec side
#: tables are kept for their schema and shipped EMPTY — their rows are outputs of a run,
#: not inputs to it, and rank_firm regenerates every one before the exporters read them.
KEEP_TABLES = (
    "tenders",
    "notice_trades",
    "firms",
    "firm_notice_scores",
    "firm_notice_exclusions",
    "municipalities",
    "notice_documents",
)

#: Kept for schema, emptied of rows.
EMPTY_TABLES = ("firm_notice_scores", "firm_notice_exclusions")

#: Tables whose row counts must survive the projection untouched.
VERIFY_TABLES = ("tenders", "notice_trades", "firms", "municipalities", "notice_documents")

PROVENANCE_TABLE = "slim_provenance"

PROVENANCE_DDL = f"""
CREATE TABLE {PROVENANCE_TABLE} (
    generated_at TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size_bytes INTEGER NOT NULL,
    source_max_ingested_at TEXT,
    source_max_updated_at TEXT,
    source_bid_interactions INTEGER,
    source_firm_entities INTEGER,
    scale_estimator_generated_at TEXT,
    scale_estimator_git_revision TEXT,
    scale_estimator_corpus_awards INTEGER,
    booster_sha256 TEXT,
    booster_trees INTEGER,
    firms_built_at TEXT,
    git_revision TEXT,
    tables_dropped TEXT NOT NULL,
    tables_emptied TEXT NOT NULL
)
"""


def git_revision() -> str | None:
    """Current git revision, or None outside a repository."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("Could not read the git revision: %s", exc)
        return None
    return completed.stdout.strip() or None


def file_sha256(path: Path) -> str:
    """Content hash of a file, streamed."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _count(connection: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.OperationalError:
        return None


def _scalar(connection: sqlite3.Connection, sql: str) -> Any:
    try:
        row = connection.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def retrain_generation() -> dict[str, Any]:
    """Which fitted artifacts this slim database is meant to be paired with.

    Read from the committed artifacts rather than from the database, because that is
    where the identity actually lives: the estimator that produced every scale_band and
    the booster that will serve every ranking.
    """
    facts: dict[str, Any] = {
        "scale_estimator_generated_at": None,
        "scale_estimator_git_revision": None,
        "scale_estimator_corpus_awards": None,
        "booster_sha256": None,
        "booster_trees": None,
        "firms_built_at": None,
    }

    estimator = Path(config.PROJECT_ROOT) / "model" / "artifacts" / "scale-estimator.json"
    if estimator.is_file():
        try:
            with estimator.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            facts["scale_estimator_generated_at"] = payload.get("generated_at")
            facts["scale_estimator_git_revision"] = payload.get("git_revision")
            facts["scale_estimator_corpus_awards"] = (payload.get("corpus") or {}).get(
                "awards"
            )
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not read the scale estimator: %s", exc)

    manifest = Path(config.PROJECT_ROOT) / "web" / "data" / "model" / "manifest.json"
    if manifest.is_file():
        try:
            with manifest.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            facts["booster_sha256"] = (payload.get("model") or {}).get("booster_sha256")
            facts["booster_trees"] = (payload.get("model") or {}).get("trees")
            facts["firms_built_at"] = (payload.get("firms") or {}).get("built_at")
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not read the serving manifest: %s", exc)

    return facts


def build(
    source: Path | str | None = None,
    dest: Path | str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Project ``source`` down to the daily-path tables and stamp the result."""
    source_path = Path(source) if source else Path(config.NOTICES_DB_PATH)
    dest_path = Path(dest) if dest else DEFAULT_DEST

    if not source_path.is_file():
        raise SystemExit(f"No source database at {source_path}")
    if source_path.resolve() == dest_path.resolve():
        raise SystemExit("Source and destination are the same file")
    if dest_path.exists() and not overwrite:
        raise SystemExit(
            f"{dest_path} already exists. Pass --overwrite to replace it — the slim "
            "database is disposable and regenerating it is the expected move, but "
            "clobbering one silently is not."
        )

    source_connection = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        present = _tables(source_connection)
        if PROVENANCE_TABLE in present:
            raise SystemExit(
                f"{source_path} carries {PROVENANCE_TABLE}, so it is already a slim "
                "database. Projecting a projection would stamp the copy rather than "
                "the retrain it came from. Build from the full database instead."
            )
        missing = [name for name in KEEP_TABLES if name not in present]
        if missing:
            raise SystemExit(
                f"{source_path} is missing tables the daily path needs: "
                f"{', '.join(missing)}"
            )

        before = {name: _count(source_connection, name) for name in VERIFY_TABLES}
        source_facts = {
            "source_max_ingested_at": _scalar(
                source_connection, "SELECT MAX(ingested_at) FROM tenders"
            ),
            "source_max_updated_at": _scalar(
                source_connection, "SELECT MAX(updated_at) FROM tenders"
            ),
            "source_bid_interactions": _count(source_connection, "bid_interactions"),
            "source_firm_entities": _count(source_connection, "firm_entities"),
        }
        dropped = sorted(present - set(KEEP_TABLES) - {"sqlite_sequence"})

        LOGGER.info("Phase 1/3: VACUUM INTO %s", dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_path.exists():
            dest_path.unlink()
        source_connection.execute("VACUUM INTO ?", (str(dest_path),))
    finally:
        source_connection.close()

    copied_bytes = dest_path.stat().st_size
    connection = sqlite3.connect(dest_path)
    try:
        LOGGER.info("Phase 2/3: dropping %d table(s): %s", len(dropped), ", ".join(dropped))
        with connection:
            for name in dropped:
                connection.execute(f'DROP TABLE IF EXISTS "{name}"')
            for name in EMPTY_TABLES:
                connection.execute(f'DELETE FROM "{name}"')

        after = {name: _count(connection, name) for name in VERIFY_TABLES}
        changed = {n: (before[n], after[n]) for n in VERIFY_TABLES if before[n] != after[n]}
        if changed:
            raise RuntimeError(
                "Row counts changed during the projection, which must only drop whole "
                f"tables and empty two: {changed}"
            )

        with connection:
            connection.execute(PROVENANCE_DDL)
            connection.execute(
                f"INSERT INTO {PROVENANCE_TABLE} VALUES ("
                + ", ".join("?" for _ in range(17))
                + ")",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    str(source_path),
                    file_sha256(source_path),
                    source_path.stat().st_size,
                    source_facts["source_max_ingested_at"],
                    source_facts["source_max_updated_at"],
                    source_facts["source_bid_interactions"],
                    source_facts["source_firm_entities"],
                    *(
                        retrain_generation()[key]
                        for key in (
                            "scale_estimator_generated_at",
                            "scale_estimator_git_revision",
                            "scale_estimator_corpus_awards",
                            "booster_sha256",
                            "booster_trees",
                            "firms_built_at",
                        )
                    ),
                    git_revision(),
                    json.dumps(dropped),
                    json.dumps(list(EMPTY_TABLES)),
                ),
            )

        # Phase 3 must run outside a transaction, and is what actually shrinks the file.
        LOGGER.info("Phase 3/3: VACUUM to reclaim the dropped pages")
        connection.execute("VACUUM")
    finally:
        connection.close()

    final_bytes = dest_path.stat().st_size
    summary = {
        "source": str(source_path),
        "dest": str(dest_path),
        "source_mb": round(source_path.stat().st_size / 1_048_576, 1),
        "copied_mb": round(copied_bytes / 1_048_576, 1),
        "slim_mb": round(final_bytes / 1_048_576, 1),
        "reduction": f"{100 * (1 - final_bytes / source_path.stat().st_size):.1f}%",
        "tables_kept": list(KEEP_TABLES),
        "tables_dropped": dropped,
        "tables_emptied": list(EMPTY_TABLES),
        "rows": after,
        "provenance": read_provenance(dest_path),
    }
    LOGGER.info(
        "Slim database: %.1f MB from %.1f MB (%s smaller)",
        final_bytes / 1_048_576,
        source_path.stat().st_size / 1_048_576,
        summary["reduction"],
    )
    return summary


def read_provenance(path: Path | str) -> dict[str, Any] | None:
    """The stamp, or None when the database carries none."""
    connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if PROVENANCE_TABLE not in _tables(connection):
            return None
        row = connection.execute(f"SELECT * FROM {PROVENANCE_TABLE}").fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the committed slim database from the full one"
    )
    parser.add_argument("--source", default=None, help="full database (default: config)")
    parser.add_argument("--dest", default=None, help=f"output (default: {DEFAULT_DEST})")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the stamp of an existing slim database and exit",
    )
    args = parser.parse_args()

    if args.show:
        target = Path(args.dest) if args.dest else DEFAULT_DEST
        stamp = read_provenance(target)
        if stamp is None:
            raise SystemExit(f"{target} carries no {PROVENANCE_TABLE} stamp")
        print(json.dumps(stamp, indent=2))
        return

    summary = build(args.source, args.dest, args.overwrite)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
