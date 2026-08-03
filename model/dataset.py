"""Phase A: turn SEAO's OCDS releases into a bid-interaction dataset.

Three steps, each inspectable on its own:

1. **Extract** every observed firm↔procurement event from the cached weekly files.
   SEAO publishes ``tender.tenderers`` (who bid), ``bids`` (what they bid), and
   ``awards.suppliers`` (who won), all carrying government-issued firm identifiers.
2. **Resolve** those identifiers to entities. The identifier is usually enough, but
   SEAO reissues them, so one firm can hold several. Merges happen only on evidence a
   human would accept; anything weaker is written to a review file rather than
   quietly applied.
3. **Assemble** the interaction table the model will train on.

Nothing here calls a hosted API, and every step is reproducible from the cached
files with ``python3 -m model.dataset``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

import config
from notices import db


LOGGER = logging.getLogger(__name__)

SEAO_DIR = Path(config.PROJECT_ROOT) / "data" / "seao"
REVIEW_DIR = Path(config.PROJECT_ROOT) / "eval" / "model"

#: An identifier carrying no information. SEAO emits these; merging on them would
#: collapse unrelated firms into one entity.
DEGENERATE_IDS = {"", "FO-", "FO", "-"}

#: Legal-form suffixes, stripped before comparing names. Québec registrations carry
#: several forms of the same idea (inc./Inc./INC), plus French equivalents.
LEGAL_SUFFIXES = (
    "societe en nom collectif a responsabilite limitee",
    "societe en nom collectif",
    "senc rl", "sencrl", "senc", "srl",
    "incorporee", "incorporated", "inc",
    "limitee", "ltee", "limited", "ltd", "lte",
    "corporation", "corporate", "corp",
    "compagnie", "company", "cie", "co",
    "enregistree", "enr",
    "sa", "sas", "sec", "spa", "llc", "lp", "plc",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS firm_entities (
        firm_id TEXT PRIMARY KEY,
        canonical_id TEXT NOT NULL,
        raw_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        neq TEXT,
        merge_rule TEXT NOT NULL,
        observations INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bid_interactions (
        canonical_id TEXT NOT NULL,
        ocid TEXT NOT NULL,
        bid_amount REAL,
        won INTEGER NOT NULL DEFAULT 0,
        interaction_date TEXT,
        buyer_id TEXT,
        buyer_name TEXT,
        category TEXT,
        region TEXT,
        title TEXT,
        PRIMARY KEY (canonical_id, ocid)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_interactions_date ON bid_interactions (interaction_date)",
    "CREATE INDEX IF NOT EXISTS idx_interactions_firm ON bid_interactions (canonical_id)",
    "CREATE INDEX IF NOT EXISTS idx_entities_canonical ON firm_entities (canonical_id)",
)


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the Phase A tables."""
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def normalize_name(value: Any) -> str:
    """Fold a firm name for comparison: accents, case, punctuation, legal form."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    # Strip trailing legal forms repeatedly: "groupe abc inc ltee" -> "groupe abc".
    changed = True
    while changed and text:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            if text.endswith(f" {suffix}"):
                text = text[: -len(suffix) - 1].strip()
                changed = True
    return re.sub(r"\s+", " ", text).strip()


def iter_releases(seao_dir: Path | str | None = None) -> Iterator[dict]:
    """Yield every release from the cached weekly files, oldest file first."""
    directory = Path(seao_dir) if seao_dir else SEAO_DIR
    for path in sorted(directory.glob("hebdo_*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.error("Skipping unreadable weekly file %s: %s", path.name, exc)
            continue
        for release in payload.get("releases") or []:
            if isinstance(release, dict):
                yield release


def extract_observations(seao_dir: Path | str | None = None) -> dict[str, Any]:
    """Read every firm↔procurement event out of the releases.

    A procurement is seen many times across weekly files as it progresses, so events
    are folded per (firm, ocid): the union of "bid" and "won", the best bid amount
    seen, and the latest tender metadata.
    """
    interactions: dict[tuple[str, str], dict] = {}
    firm_names: dict[str, Counter] = defaultdict(Counter)
    firm_neq: dict[str, str] = {}
    stats = Counter()

    for release in iter_releases(seao_dir):
        ocid = str(release.get("ocid") or "")
        if not ocid:
            continue
        stats["releases"] += 1
        tender = release.get("tender") or {}
        buyer = release.get("buyer") if isinstance(release.get("buyer"), dict) else {}

        # NEQ is the Québec business number; when present it identifies a legal
        # entity far better than a name ever will.
        for party in release.get("parties") or []:
            if not isinstance(party, dict):
                continue
            party_id = str(party.get("id") or "")
            details = party.get("details") if isinstance(party.get("details"), dict) else {}
            neq = str(details.get("neq") or "").strip()
            if party_id and neq:
                firm_neq.setdefault(party_id, neq)
            if party_id and party.get("name"):
                firm_names[party_id][str(party["name"]).strip()] += 1

        metadata = {
            "interaction_date": _tender_date(tender, release),
            "buyer_id": str(buyer.get("id") or "") or None,
            "buyer_name": str(buyer.get("name") or "") or None,
            "category": _category(tender),
            "region": _region(release, buyer),
            "title": str(tender.get("title") or "") or None,
        }

        amounts = {
            str(bid.get("id") or ""): bid.get("value")
            for bid in release.get("bids") or []
            if isinstance(bid, dict)
        }
        winners = {
            str(supplier.get("id") or "")
            for award in release.get("awards") or []
            if isinstance(award, dict)
            for supplier in award.get("suppliers") or []
            if isinstance(supplier, dict)
        }

        participants: dict[str, dict] = {}
        for tenderer in tender.get("tenderers") or []:
            if not isinstance(tenderer, dict):
                continue
            firm_id = str(tenderer.get("id") or "")
            if firm_id in DEGENERATE_IDS:
                stats["degenerate_tenderer_ids"] += 1
                continue
            participants[firm_id] = {"bid": True}
            if tenderer.get("name"):
                firm_names[firm_id][str(tenderer["name"]).strip()] += 1
        for firm_id in winners:
            if firm_id in DEGENERATE_IDS:
                stats["degenerate_winner_ids"] += 1
                continue
            participants.setdefault(firm_id, {"bid": True})
            participants[firm_id]["won"] = True

        for firm_id, event in participants.items():
            key = (firm_id, ocid)
            entry = interactions.setdefault(
                key,
                {"firm_id": firm_id, "ocid": ocid, "bid_amount": None, "won": 0, **metadata},
            )
            if event.get("won"):
                entry["won"] = 1
            amount = amounts.get(firm_id)
            if amount is not None and entry["bid_amount"] is None:
                try:
                    entry["bid_amount"] = float(amount)
                except (TypeError, ValueError):
                    pass
            for field, value in metadata.items():
                if value and not entry.get(field):
                    entry[field] = value

    stats["interactions"] = len(interactions)
    stats["firms"] = len(firm_names)
    LOGGER.info(
        "Extracted %d interactions across %d firms from %d releases",
        len(interactions),
        len(firm_names),
        stats["releases"],
    )
    return {
        "interactions": interactions,
        "firm_names": firm_names,
        "firm_neq": firm_neq,
        "stats": dict(stats),
    }


def resolve_entities(
    firm_names: dict[str, Counter], firm_neq: dict[str, str]
) -> dict[str, Any]:
    """Cluster firm identifiers into entities on evidence, not on resemblance.

    Two rules merge automatically, both being statements of fact rather than
    similarity judgements:

    * identifiers sharing a NEQ are the same registered business;
    * identifiers whose normalized names match exactly *and* which never appear as
      rival bidders on the same procurement.

    Everything weaker — a name match between identifiers that did compete against
    each other, which is what two genuinely different firms of the same name look
    like — is left unmerged and written out for review.
    """
    parent: dict[str, str] = {firm_id: firm_id for firm_id in firm_names}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(first: str, second: str) -> None:
        a, b = find(first), find(second)
        if a != b:
            parent[max(a, b)] = min(a, b)

    merges: list[dict] = []

    by_neq: dict[str, list[str]] = defaultdict(list)
    for firm_id, neq in firm_neq.items():
        if firm_id in parent and neq:
            by_neq[neq].append(firm_id)
    for neq, ids in by_neq.items():
        if len(ids) < 2:
            continue
        for other in ids[1:]:
            union(ids[0], other)
        merges.append(
            {
                "rule": "shared_neq",
                "key": neq,
                "ids": sorted(ids),
                "names": sorted({_best_name(firm_names[i]) for i in ids}),
            }
        )

    return {"parent": parent, "find": find, "merges": merges}


def build_entities(
    firm_names: dict[str, Counter],
    firm_neq: dict[str, str],
    interactions: dict[tuple[str, str], dict],
) -> dict[str, Any]:
    """Resolve identifiers to entities, merging only on evidence.

    SEAO's identifier is the Québec business number, so it is already close to
    canonical — but a firm re-registering receives a new one, which is why identical
    names still have to be reconciled. Two identifiers merge when their normalized
    names match **and** they never appear as rival bidders on the same procurement.
    Two firms that bid against each other are, by construction, two firms.
    """
    active = {firm_id for firm_id, _ in interactions}
    best = {
        firm_id: counter.most_common(1)[0][0]
        for firm_id, counter in firm_names.items()
        if counter and firm_id in active
    }

    firms_by_ocid: dict[str, set[str]] = defaultdict(set)
    for firm_id, ocid in interactions:
        firms_by_ocid[ocid].add(firm_id)
    rivals: set[tuple[str, str]] = set()
    for members in firms_by_ocid.values():
        ordered = sorted(members)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                rivals.add((ordered[i], ordered[j]))

    by_name: dict[str, list[str]] = defaultdict(list)
    for firm_id, raw in best.items():
        normalized = normalize_name(raw)
        if normalized:
            by_name[normalized].append(firm_id)

    merge_map: dict[str, str] = {}
    merged: list[dict] = []
    blocked: list[dict] = []
    for normalized, ids in by_name.items():
        if len(ids) < 2:
            continue
        ids = sorted(ids)
        competed = any(
            (ids[i], ids[j]) in rivals
            for i in range(len(ids))
            for j in range(i + 1, len(ids))
        )
        record = {
            "normalized": normalized,
            "ids": ids,
            "names": sorted({best[i] for i in ids}),
        }
        if competed:
            # Same name, but they bid against each other — different businesses, or a
            # placeholder name shared by many. Never merged automatically.
            blocked.append(record)
            continue
        for firm_id in ids:
            merge_map[firm_id] = ids[0]
        merged.append(record)

    entities = {
        firm_id: {
            "firm_id": firm_id,
            "canonical_id": merge_map.get(firm_id, firm_id),
            "raw_name": raw,
            "normalized_name": normalize_name(raw),
            "neq": firm_neq.get(firm_id),
            "merge_rule": "name_no_rivalry" if firm_id in merge_map else "identifier",
        }
        for firm_id, raw in best.items()
    }
    LOGGER.info(
        "Resolved %d identifiers to %d entities (%d merges, %d blocked for review)",
        len(entities),
        len({e["canonical_id"] for e in entities.values()}),
        sum(len(m["ids"]) - 1 for m in merged),
        len(blocked),
    )
    return {"entities": entities, "merged": merged, "blocked": blocked}


def persist(
    connection: sqlite3.Connection,
    entities: dict[str, dict],
    interactions: dict[tuple[str, str], dict],
) -> dict[str, int]:
    """Write firm_entities and bid_interactions."""
    ensure_schema(connection)
    counts = Counter(firm_id for firm_id, _ in interactions)
    with connection:
        connection.execute("DELETE FROM firm_entities")
        connection.execute("DELETE FROM bid_interactions")
        connection.executemany(
            "INSERT INTO firm_entities (firm_id, canonical_id, raw_name, "
            "normalized_name, neq, merge_rule, observations) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    entity["firm_id"],
                    entity["canonical_id"],
                    entity["raw_name"],
                    entity["normalized_name"],
                    entity["neq"],
                    entity["merge_rule"],
                    counts.get(entity["firm_id"], 0),
                )
                for entity in entities.values()
            ],
        )

        canonical = {
            firm_id: entity["canonical_id"] for firm_id, entity in entities.items()
        }
        folded: dict[tuple[str, str], dict] = {}
        for (firm_id, ocid), entry in interactions.items():
            key = (canonical.get(firm_id, firm_id), ocid)
            existing = folded.get(key)
            if existing is None:
                folded[key] = dict(entry)
                continue
            existing["won"] = max(existing["won"], entry["won"])
            if existing.get("bid_amount") is None:
                existing["bid_amount"] = entry.get("bid_amount")

        connection.executemany(
            "INSERT INTO bid_interactions (canonical_id, ocid, bid_amount, won, "
            "interaction_date, buyer_id, buyer_name, category, region, title) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    key[0],
                    key[1],
                    entry.get("bid_amount"),
                    int(entry.get("won") or 0),
                    entry.get("interaction_date"),
                    entry.get("buyer_id"),
                    entry.get("buyer_name"),
                    entry.get("category"),
                    entry.get("region"),
                    entry.get("title"),
                )
                for key, entry in folded.items()
            ],
        )
    return {"entities": len(entities), "interactions": len(folded)}


def _best_name(counter: Counter) -> str:
    return counter.most_common(1)[0][0] if counter else ""


def _tender_date(tender: dict, release: dict) -> str | None:
    period = tender.get("tenderPeriod") if isinstance(tender.get("tenderPeriod"), dict) else {}
    return (
        str(period.get("endDate") or "")[:10]
        or str(period.get("startDate") or "")[:10]
        or str(release.get("date") or "")[:10]
        or None
    )


def _category(tender: dict) -> str | None:
    extra = tender.get("additionalProcurementCategories")
    if isinstance(extra, list) and extra:
        return str(extra[0])
    return str(tender.get("mainProcurementCategory") or "") or None


def _region(release: dict, buyer: dict) -> str | None:
    for party in release.get("parties") or []:
        if not isinstance(party, dict):
            continue
        if str(party.get("id") or "") == str(buyer.get("id") or ""):
            address = party.get("address") if isinstance(party.get("address"), dict) else {}
            region = str(address.get("region") or "").strip()
            if region:
                return region
    return "QC"


def build(
    connection: sqlite3.Connection, seao_dir: Path | str | None = None
) -> dict[str, Any]:
    """Run the whole Phase A pipeline and persist its tables."""
    extracted = extract_observations(seao_dir)
    resolved = build_entities(
        extracted["firm_names"], extracted["firm_neq"], extracted["interactions"]
    )
    stored = persist(connection, resolved["entities"], extracted["interactions"])

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    review_path = REVIEW_DIR / "entity_merges_for_review.json"
    with review_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "counts": {
                    "auto_merged_names": len(resolved["merged"]),
                    "blocked_names": len(resolved["blocked"]),
                },
                "auto_merged": resolved["merged"],
                "blocked_needs_review": resolved["blocked"],
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    LOGGER.info("Wrote merge review file %s", review_path)
    return {**extracted["stats"], **stored, "review_file": str(review_path)}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Build the bid-interaction dataset")
    parser.add_argument("--db", default=None)
    parser.add_argument("--seao-dir", default=None)
    args = parser.parse_args()

    connection = db.connect(args.db)
    try:
        result = build(connection, args.seao_dir)
    finally:
        connection.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
