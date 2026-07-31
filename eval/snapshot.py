"""Snapshot a firm's top-N recommendations for hand labelling.

Each snapshot is a timestamped, immutable record of what the engine returned and
which versions produced it, with an empty ``label`` on every row. Labelling those by
hand is what turns a pile of snapshots into a precision@k series across versions, so
the file deliberately carries enough provenance (weights, mapping, and git revision)
to explain a change in the numbers later.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from matchrec import rank, schema, scoring, timeutil, trades


LOGGER = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(config.PROJECT_ROOT) / "eval" / "snapshots"

#: Labels a reviewer may assign. ``null`` means "not yet labelled".
LABEL_VALUES = ("relevant", "marginal", "irrelevant")

#: Columns of the hand-labelling CSV. Only ``label`` is read back; the rest are
#: context for the reviewer and identity for matching rows to the snapshot.
CSV_COLUMNS = (
    "rank",
    "score",
    "title",
    "buyer_name",
    "closing_date",
    "flags",
    "notice_url",
    "label",
)

#: Excel and Numbers both assume the system codepage without a BOM, which mangles
#: French titles ("Réfection" -> "RÃ©fection"). utf-8-sig writes the BOM and is
#: transparent when reading files that lack one.
CSV_ENCODING = "utf-8-sig"

FLAG_SEPARATOR = "|"

#: The cohort split the labelling exercise exists to measure.
COHORT_UNMAPPED_FLAG = "trade_unmapped"
COHORT_MAPPED = "mapped"
COHORT_UNMAPPED = "unmapped"


def git_revision() -> str | None:
    """Return the current git revision, or None outside a repository."""
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
    revision = completed.stdout.strip()
    return revision or None


def build_snapshot(result: dict, top: int, taken_at: datetime | None = None) -> dict:
    """Build the snapshot payload from a ranking result."""
    export = rank.to_export(result, top)
    stamp = (taken_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "snapshot_version": 1,
        "taken_at": stamp.isoformat(timespec="seconds"),
        "git_revision": git_revision(),
        "weights_version": export["weights_version"],
        "mapping_version": export["mapping_version"],
        "firm": export["firm"],
        "top": top,
        "candidate_count": export["candidate_count"],
        "excluded_count": export["excluded_count"],
        "label_values": list(LABEL_VALUES),
        "labelled": False,
        "results": [
            {
                **row,
                # Filled in by a human; precision@k is computed from these.
                "label": None,
                "label_note": None,
            }
            for row in export["results"]
        ],
    }


def write_snapshot(
    payload: dict, directory: Path | str | None = None, path: Path | str | None = None
) -> Path:
    """Write a snapshot to a timestamped file and return its path."""
    if path is not None:
        destination = Path(path)
    else:
        folder = Path(directory) if directory else SNAPSHOT_DIR
        stamp = str(payload["taken_at"]).replace(":", "").replace("-", "")
        destination = folder / f"firm{payload['firm']['id']}-{stamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    LOGGER.info("Wrote snapshot %s", destination)
    return destination


def precision_at_k(payload: dict, k: int | None = None) -> dict[str, Any]:
    """Compute precision@k over a labelled snapshot.

    Returns ``{"labelled": n, "k": k, "precision": p}`` with ``precision`` None while
    nothing has been labelled, so an unlabelled snapshot reports honestly instead of
    scoring 0 or 1 by accident.
    """
    rows = payload.get("results") or []
    limit = k or len(rows)
    considered = rows[:limit]
    labelled = [row for row in considered if row.get("label") in LABEL_VALUES]
    if not labelled:
        return {"labelled": 0, "k": limit, "precision": None}
    relevant = sum(1 for row in labelled if row["label"] == "relevant")
    return {
        "labelled": len(labelled),
        "k": limit,
        "precision": round(relevant / len(labelled), 4),
    }


def csv_path_for(snapshot_path: Path | str) -> Path:
    """Return the CSV that sits beside a snapshot JSON."""
    return Path(snapshot_path).with_suffix(".csv")


def snapshot_path_for(csv_path: Path | str) -> Path:
    """Return the snapshot JSON that sits beside a labelling CSV."""
    return Path(csv_path).with_suffix(".json")


def to_csv_rows(payload: dict) -> list[dict]:
    """Flatten a snapshot's results into hand-labelling CSV rows."""
    rows: list[dict] = []
    for row in payload.get("results") or []:
        rows.append(
            {
                "rank": row.get("rank"),
                "score": row.get("final_score"),
                "title": row.get("title") or "",
                "buyer_name": row.get("buyer_name") or "",
                "closing_date": row.get("closing_date_utc") or "",
                "flags": FLAG_SEPARATOR.join(
                    str(flag) for flag in row.get("flags") or []
                ),
                "notice_url": row.get("notice_url") or "",
                # Carry any existing label so export -> edit -> import -> export
                # never silently discards work already done.
                "label": row.get("label") or "",
            }
        )
    return rows


def write_csv(payload: dict, path: Path | str) -> Path:
    """Write the labelling CSV for a snapshot and return its path."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(to_csv_rows(payload))
    LOGGER.info("Wrote labelling CSV %s", destination)
    return destination


def export_csv(
    snapshot_path: Path | str, csv_path: Path | str | None = None
) -> tuple[Path, dict]:
    """Read a snapshot and write its sibling labelling CSV."""
    source = Path(snapshot_path)
    payload = read_snapshot(source)
    destination = Path(csv_path) if csv_path else csv_path_for(source)
    write_csv(payload, destination)
    return destination, payload


def read_csv_labels(csv_path: Path | str) -> list[dict]:
    """Read and validate labelling rows from a CSV.

    Raises ``ValueError`` naming the offending line for a missing column or a label
    outside the vocabulary, rather than importing a partially understood file.
    """
    source = Path(csv_path)
    with source.open(encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [
            column
            for column in ("rank", "notice_url", "label")
            if column not in fieldnames
        ]
        if missing:
            raise ValueError(
                f"{source.name} is missing required column(s): {', '.join(missing)}"
            )

        rows: list[dict] = []
        for line, raw in enumerate(reader, start=2):
            label = str(raw.get("label") or "").strip().casefold()
            if label and label not in LABEL_VALUES:
                raise ValueError(
                    f"{source.name} line {line}: label {raw.get('label')!r} is not one "
                    f"of {', '.join(LABEL_VALUES)} (leave blank for unlabelled)"
                )
            rank_text = str(raw.get("rank") or "").strip()
            try:
                rank_value = int(rank_text)
            except ValueError as exc:
                raise ValueError(
                    f"{source.name} line {line}: rank {rank_text!r} is not a number"
                ) from exc
            rows.append(
                {
                    "line": line,
                    "rank": rank_value,
                    "notice_url": str(raw.get("notice_url") or "").strip(),
                    "label": label or None,
                    "label_note": (raw.get("label_note") or "").strip() or None,
                }
            )
    return rows


def apply_csv_labels(payload: dict, rows: list[dict]) -> dict:
    """Merge labelling rows into a snapshot payload in place.

    Rows are matched on rank *and* notice URL, so a CSV from a different snapshot —
    or one whose rows were sorted or deleted in a spreadsheet — is refused rather
    than silently applied to the wrong tenders.
    """
    results = payload.get("results") or []
    if len(rows) != len(results):
        raise ValueError(
            f"CSV has {len(rows)} row(s) but the snapshot has {len(results)}; "
            "re-export the CSV rather than adding or removing rows"
        )

    by_rank = {int(row.get("rank", -1)): row for row in results}
    for entry in rows:
        target = by_rank.get(entry["rank"])
        if target is None:
            raise ValueError(
                f"line {entry['line']}: rank {entry['rank']} is not in this snapshot"
            )
        expected = str(target.get("notice_url") or "")
        if entry["notice_url"] != expected:
            raise ValueError(
                f"line {entry['line']}: notice_url does not match rank "
                f"{entry['rank']} in this snapshot "
                f"({entry['notice_url']!r} vs {expected!r}); the CSV and snapshot "
                "are out of step"
            )

    tally = {"labelled": 0, "cleared": 0, "unchanged": 0}
    for entry in rows:
        target = by_rank[entry["rank"]]
        previous = target.get("label")
        if previous == entry["label"]:
            tally["unchanged"] += 1
        elif entry["label"] is None:
            tally["cleared"] += 1
        else:
            tally["labelled"] += 1
        target["label"] = entry["label"]
        if entry["label_note"] is not None:
            target["label_note"] = entry["label_note"]

    payload["labelled"] = bool(results) and all(
        row.get("label") in LABEL_VALUES for row in results
    )
    tally["labelled_total"] = sum(
        1 for row in results if row.get("label") in LABEL_VALUES
    )
    tally["rows"] = len(results)
    return tally


def import_csv(
    csv_path: Path | str, snapshot_path: Path | str | None = None
) -> tuple[Path, dict]:
    """Read labels from a CSV back into its snapshot, writing only on change."""
    source = Path(csv_path)
    destination = Path(snapshot_path) if snapshot_path else snapshot_path_for(source)
    if not destination.is_file():
        raise ValueError(
            f"No snapshot found at {destination}; --from-csv expects the CSV to sit "
            "beside the JSON it came from"
        )

    payload = read_snapshot(destination)
    before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    tally = apply_csv_labels(payload, read_csv_labels(source))
    after = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    if before == after:
        LOGGER.info("Labels in %s already match %s", source.name, destination.name)
        tally["written"] = False
        return destination, tally

    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    LOGGER.info("Applied %d label(s) to %s", tally["labelled"], destination.name)
    tally["written"] = True
    return destination, tally


def read_snapshot(path: Path | str) -> dict:
    """Load a snapshot JSON."""
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a snapshot document")
    return payload


def cohort_of(row: dict) -> str:
    """Which labelling cohort a result belongs to."""
    flags = row.get("flags") or []
    return COHORT_UNMAPPED if COHORT_UNMAPPED_FLAG in flags else COHORT_MAPPED


def label_breakdown(rows: list[dict]) -> dict[str, int]:
    """Count each label across rows, including the unlabelled remainder."""
    counts = {label: 0 for label in LABEL_VALUES}
    counts["unlabelled"] = 0
    for row in rows:
        label = row.get("label")
        if label in LABEL_VALUES:
            counts[label] += 1
        else:
            counts["unlabelled"] += 1
    return counts


def precision_report(payload: dict, k: int | None = None) -> dict:
    """Precision@k overall and split by trade-mapping cohort.

    The mapped/unmapped split is the comparison the labelling exists to settle:
    unmapped notices are kept on purpose and credited 0.3 (or 0.15), and only
    labels can say whether that band earns its place.
    """
    rows = payload.get("results") or []
    limit = k or len(rows)
    considered = rows[:limit]

    def measure(subset: list[dict]) -> dict:
        labelled = [row for row in subset if row.get("label") in LABEL_VALUES]
        relevant = sum(1 for row in labelled if row["label"] == "relevant")
        return {
            "rows": len(subset),
            "labelled": len(labelled),
            "precision": round(relevant / len(labelled), 4) if labelled else None,
            "breakdown": label_breakdown(subset),
        }

    return {
        "k": limit,
        "overall": measure(considered),
        "cohorts": {
            cohort: measure([row for row in considered if cohort_of(row) == cohort])
            for cohort in (COHORT_MAPPED, COHORT_UNMAPPED)
        },
    }


def snapshot_firm(
    firm_id: int,
    top: int = 20,
    db_path: Any = None,
    directory: Path | str | None = None,
    path: Path | str | None = None,
) -> tuple[Path, dict]:
    """Rank a firm and write the resulting snapshot."""
    connection = schema.connect(db_path)
    try:
        weights = scoring.load_weights()
        mapping = trades.load_mapping()
        rank.prepare(connection, mapping)
        result = rank.rank_firm(
            connection, firm_id, weights=weights, mapping=mapping, now=timeutil.now_utc()
        )
    finally:
        connection.close()
    payload = build_snapshot(result, top)
    return write_snapshot(payload, directory=directory, path=path), payload


def _print_score(path: str, payload: dict, report: dict) -> None:
    name = Path(path).name
    overall = report["overall"]
    if overall["precision"] is None:
        print(f"{name}: nothing labelled yet ({overall['rows']} rows awaiting labels)")
    else:
        print(
            f"{name}: precision@{report['k']} = {overall['precision']:.2f} "
            f"over {overall['labelled']} labelled row(s) "
            f"(weights {payload.get('weights_version')}, "
            f"mapping {payload.get('mapping_version')})"
        )
    print(f"  {'':<10}{'rows':>6}{'labelled':>10}{'precision':>11}   breakdown")
    for label, cohort in (
        ("overall", overall),
        (COHORT_MAPPED, report["cohorts"][COHORT_MAPPED]),
        (COHORT_UNMAPPED, report["cohorts"][COHORT_UNMAPPED]),
    ):
        precision = (
            f"{cohort['precision']:.2f}" if cohort["precision"] is not None else "—"
        )
        breakdown = cohort["breakdown"]
        print(
            f"  {label:<10}{cohort['rows']:>6}{cohort['labelled']:>10}"
            f"{precision:>11}   "
            + ", ".join(
                f"{name} {breakdown[name]}"
                for name in (*LABEL_VALUES, "unlabelled")
                if breakdown[name]
            )
        )
    if report["cohorts"][COHORT_UNMAPPED]["rows"]:
        print(
            f"  ({COHORT_UNMAPPED} = notices carrying the {COHORT_UNMAPPED_FLAG} flag)"
        )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot a firm's top-N recommendations for hand labelling"
    )
    parser.add_argument("--firm", type=int, default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--db", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--score",
        default=None,
        help="compute precision@k for an existing snapshot instead of taking one",
    )
    parser.add_argument(
        "--to-csv",
        default=None,
        help="write a snapshot's sibling labelling CSV",
    )
    parser.add_argument(
        "--from-csv",
        default=None,
        help="read labels from a CSV back into its snapshot",
    )
    parser.add_argument("--k", type=int, default=None)
    args = parser.parse_args()

    if args.to_csv:
        destination, payload = export_csv(args.to_csv, args.out)
        unlabelled = sum(
            1 for row in payload["results"] if row.get("label") not in LABEL_VALUES
        )
        print(f"csv: {destination}")
        print(
            f"  {len(payload['results'])} row(s), {unlabelled} awaiting labels "
            f"(UTF-8 with BOM; safe to open in Excel or Numbers)"
        )
        print("  fill the label column with one of: " + ", ".join(LABEL_VALUES))
        print(f"  then: python3 -m eval.snapshot --from-csv {destination}")
        return

    if args.from_csv:
        try:
            destination, tally = import_csv(args.from_csv, args.out)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}") from exc
        print(f"snapshot: {destination}")
        print(
            f"  {tally['labelled']} label(s) applied, {tally['cleared']} cleared, "
            f"{tally['unchanged']} unchanged "
            f"({tally['labelled_total']} of {tally['rows']} rows now labelled)"
        )
        if not tally["written"]:
            print("  no change: the snapshot already carried these labels")
        return

    if args.score:
        payload = read_snapshot(args.score)
        _print_score(args.score, payload, precision_report(payload, args.k))
        return

    if args.firm is None:
        raise SystemExit(
            "error: --firm is required to take a snapshot "
            "(or pass --score/--to-csv/--from-csv)"
        )

    destination, payload = snapshot_firm(
        args.firm, top=args.top, db_path=args.db, path=args.out
    )
    print(f"snapshot: {destination}")
    print(
        f"  firm {payload['firm']['id']} ({payload['firm']['name']}), "
        f"{len(payload['results'])} rows awaiting labels"
    )
    print(
        f"  weights {payload['weights_version']}, mapping {payload['mapping_version']}, "
        f"git {payload['git_revision']}"
    )
    print("  label each row with one of: " + ", ".join(LABEL_VALUES))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
