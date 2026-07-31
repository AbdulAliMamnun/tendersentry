"""Rank notices for a firm: Stage 1 filters, Stage 2 scoring, CLI and export."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from matchrec import filters, schema, scoring, timeutil, trades
from notices import db
from profiles import schema as profiles_schema


LOGGER = logging.getLogger(__name__)

EXPORT_DIR = Path(config.PROJECT_ROOT) / "data" / "rankings"

NOTICE_QUERY = """
SELECT t.id, t.source, t.source_id, t.title, t.buyer_name, t.buyer_type,
       t.category_raw, t.category_normalized, t.region, t.estimated_value,
       t.currency, t.closing_date, t.closing_date_utc, t.notice_url,
       t.documents_open, t.status,
       nt.trade_slugs, nt.slug_sources, nt.mapping_status, nt.construction_marked
FROM tenders t
LEFT JOIN notice_trades nt ON nt.tender_id = t.id
"""


def prepare(
    connection: sqlite3.Connection,
    mapping: trades.TradeMapping | None = None,
    remap: bool = False,
) -> dict:
    """Bring derived data up to date: UTC deadlines and trade mapping."""
    schema.ensure_schema(connection)
    times = schema.backfill_closing_dates(connection)

    mapping = mapping or trades.load_mapping()
    stored = connection.execute(
        "SELECT COUNT(*) AS total, "
        "       COUNT(DISTINCT mapping_version) AS versions, "
        "       MIN(mapping_version) AS version FROM notice_trades"
    ).fetchone()
    notices_total = connection.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    needs_mapping = (
        remap
        or int(stored["total"] or 0) != int(notices_total or 0)
        or int(stored["versions"] or 0) != 1
        or str(stored["version"]) != mapping.version
    )
    if needs_mapping:
        mapped = trades.map_notices(connection, mapping)
    else:
        LOGGER.info(
            "Trade mapping %s already current for %d notices", mapping.version, notices_total
        )
        mapped = {"mapping_version": mapping.version, "notices": notices_total}
    return {"closing_dates": times, "mapping": mapped, "mapping_object": mapping}


def rank_firm(
    connection: sqlite3.Connection,
    firm_id: int,
    weights: dict | None = None,
    mapping: trades.TradeMapping | None = None,
    now: datetime | None = None,
    persist: bool = True,
) -> dict:
    """Filter and score every notice for one firm, returning ranked results."""
    firm = profiles_schema.get_firm(connection, firm_id)
    if firm is None:
        raise ValueError(f"No firm with id {firm_id}")

    weights = weights or scoring.load_weights()
    mapping = mapping or trades.load_mapping()
    reference = now or timeutil.now_utc()
    timestamp = db.utc_timestamp()
    floor_hours = scoring.min_hours_to_closing(weights)

    scored: list[dict] = []
    excluded_rows: list[dict] = []
    reason_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}

    for row in connection.execute(NOTICE_QUERY):
        notice = dict(row)
        notice["trade_slugs"] = schema.loads(notice.get("trade_slugs"), [])
        notice["slug_sources"] = schema.loads(notice.get("slug_sources"), {})
        verdict = filters.evaluate(
            notice, firm, mapping, reference, min_hours_to_closing=floor_hours
        )
        if not verdict["included"]:
            for reason in verdict["reasons"]:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            excluded_rows.append(
                {
                    "firm_id": firm_id,
                    "tender_id": int(notice["id"]),
                    "primary_reason": verdict["reasons"][0],
                    "reasons": schema.dumps(verdict["reasons"]),
                    "detail": verdict["detail"],
                }
            )
            continue

        result = scoring.score_notice(
            notice, firm, verdict["context"], weights, reference
        )
        flags = sorted(set(verdict["flags"]) | set(result["flags"]))
        for flag in flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
        scored.append(
            {
                "notice": notice,
                "context": verdict["context"],
                **result,
                # Must come after **result: score_notice returns its own "flags"
                # (value-baseline only), and this is the merged filter+scoring set.
                "flags": flags,
            }
        )

    scored.sort(key=_rank_key)

    tallies: dict[str, Any] = {}
    if persist:
        tallies["scores"] = schema.upsert_rows(
            connection,
            "firm_notice_scores",
            ("firm_id", "tender_id"),
            schema.SCORE_CONTENT_COLUMNS,
            "scored_at",
            [
                {
                    "firm_id": firm_id,
                    "tender_id": int(item["notice"]["id"]),
                    "base_score": item["base_score"],
                    "value_modifier": item["value_modifier"],
                    "final_score": item["final_score"],
                    "components": schema.dumps(item["components"]),
                    "flags": schema.dumps(item["flags"]),
                    "weights_version": str(weights["version"]),
                    "mapping_version": mapping.version,
                }
                for item in scored
            ],
            timestamp,
        )
        tallies["exclusions"] = schema.upsert_rows(
            connection,
            "firm_notice_exclusions",
            ("firm_id", "tender_id"),
            schema.EXCLUSION_CONTENT_COLUMNS,
            "evaluated_at",
            excluded_rows,
            timestamp,
        )
        schema.delete_stale(
            connection,
            "firm_notice_scores",
            firm_id,
            {int(item["notice"]["id"]) for item in scored},
        )
        schema.delete_stale(
            connection,
            "firm_notice_exclusions",
            firm_id,
            {int(row["tender_id"]) for row in excluded_rows},
        )

    LOGGER.info(
        "Firm %d: %d scored, %d excluded (%s)",
        firm_id,
        len(scored),
        len(excluded_rows),
        ", ".join(f"{key} {value}" for key, value in sorted(reason_counts.items())),
    )
    return {
        "firm": firm,
        "weights_version": str(weights["version"]),
        "mapping_version": mapping.version,
        "evaluated_at": reference.isoformat(timespec="seconds"),
        "scored": scored,
        "excluded_count": len(excluded_rows),
        "reason_counts": reason_counts,
        "flag_counts": flag_counts,
        "persisted": tallies,
    }


def top_components(item: dict, limit: int = 2) -> list[tuple[str, float]]:
    """Return the highest-contributing components for one scored notice."""
    ordered = sorted(
        item["components"].items(),
        key=lambda entry: (-entry[1]["points"], entry[0]),
    )
    return [(name, part["points"]) for name, part in ordered[:limit]]


def to_export(result: dict, top: int) -> dict:
    """Build the JSON payload a UI or eval snapshot consumes."""
    return {
        "firm": {
            "id": result["firm"]["id"],
            "name": result["firm"]["name"],
            "trades": result["firm"]["trades"],
            "regions": result["firm"]["regions"],
        },
        "weights_version": result["weights_version"],
        "mapping_version": result["mapping_version"],
        "evaluated_at": result["evaluated_at"],
        "candidate_count": len(result["scored"]),
        "excluded_count": result["excluded_count"],
        "reason_counts": result["reason_counts"],
        "flag_counts": result["flag_counts"],
        "results": [
            {
                "rank": index,
                "tender_id": item["notice"]["id"],
                "source": item["notice"]["source"],
                "source_id": item["notice"]["source_id"],
                "title": item["notice"]["title"],
                "buyer_name": item["notice"]["buyer_name"],
                "buyer_type": item["notice"]["buyer_type"],
                "region": item["notice"]["region"],
                "closing_date_utc": item["notice"]["closing_date_utc"],
                "estimated_value": item["notice"]["estimated_value"],
                "notice_url": item["notice"]["notice_url"],
                "trade_slugs": item["notice"]["trade_slugs"],
                "mapping_status": item["notice"]["mapping_status"],
                "base_score": item["base_score"],
                "value_modifier": item["value_modifier"],
                "final_score": item["final_score"],
                "components": item["components"],
                "value_detail": item["value_detail"],
                "flags": item["flags"],
            }
            for index, item in enumerate(result["scored"][:top], start=1)
        ],
    }


def _rank_key(item: dict) -> tuple:
    closing = item["notice"].get("closing_date_utc") or "9999"
    return (-item["final_score"], closing, int(item["notice"]["id"]))


def _print_table(result: dict, top: int) -> None:
    firm = result["firm"]
    print(
        f"{firm['name']} (firm {firm['id']}) — weights {result['weights_version']}, "
        f"mapping {result['mapping_version']}"
    )
    print(
        f"{len(result['scored'])} candidates after filters, "
        f"{result['excluded_count']} excluded"
    )

    headers = ["#", "score", "title", "buyer", "closing", "value", "why"]
    rows: list[list[str]] = []
    for index, item in enumerate(result["scored"][:top], start=1):
        notice = item["notice"]
        value = notice.get("estimated_value")
        modifier = item["value_modifier"]
        score_text = f"{item['final_score']:.1f}"
        if modifier:
            score_text += f" ({modifier:+.0f} val)"
        rows.append(
            [
                str(index),
                score_text,
                _truncate(str(notice.get("title") or ""), 52),
                _truncate(str(notice.get("buyer_name") or "—"), 26),
                str(notice.get("closing_date_utc") or "—")[:10],
                f"{float(value):,.0f}" if value is not None else "—",
                ", ".join(
                    f"{name.replace('_', ' ')} {points:.0f}"
                    for name, points in top_components(item)
                ),
            ]
        )

    if not rows:
        print("\n(no notices survived the filters)")
        _print_reasons(result)
        return

    widths = [
        max(len(row[index]) for row in [headers, *rows]) for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    print()
    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))
    _print_reasons(result)


def _print_reasons(result: dict) -> None:
    if result["reason_counts"]:
        print("\nexclusions by reason:")
        for reason, count in sorted(
            result["reason_counts"].items(), key=lambda item: -item[1]
        ):
            print(f"  {reason:<24} {count}")
    if result["flag_counts"]:
        print("\nflags on surviving notices:")
        for flag, count in sorted(result["flag_counts"].items(), key=lambda item: -item[1]):
            print(f"  {flag:<24} {count}")


def _truncate(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3] + "..."


def _main() -> None:
    parser = argparse.ArgumentParser(description="Rank tender notices for a firm")
    parser.add_argument("--firm", type=int, required=True)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--db", default=None)
    parser.add_argument("--weights", default=None)
    parser.add_argument("--mapping", default=None)
    parser.add_argument("--export", choices=["json"], default=None)
    parser.add_argument("--out", default=None, help="path for --export json")
    parser.add_argument(
        "--remap", action="store_true", help="re-run trade mapping before ranking"
    )
    args = parser.parse_args()

    connection = schema.connect(args.db)
    try:
        weights = scoring.load_weights(args.weights)
        mapping = trades.load_mapping(args.mapping)
        prepare(connection, mapping, remap=args.remap)
        result = rank_firm(connection, args.firm, weights=weights, mapping=mapping)
        _print_table(result, args.top)

        if args.export == "json":
            payload = to_export(result, args.top)
            if args.out:
                destination = Path(args.out)
            else:
                stamp = result["evaluated_at"].replace(":", "").replace("-", "")
                EXPORT_DIR.mkdir(parents=True, exist_ok=True)
                destination = EXPORT_DIR / f"firm{args.firm}-{stamp}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print(f"\nexported {len(payload['results'])} results to {destination}")
    finally:
        connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
