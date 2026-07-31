"""Map raw notice categories and text onto the trade-slug vocabulary.

Deterministic and data-driven: all knowledge lives in ``trade_mapping.json``, which
can be extended without touching this module. A notice can carry several slugs; the
one from the lowest-priority rule is primary. ``non_construction`` is only reachable
for notices that no trade rule matched and whose own category does not mark them as
construction work, so a real construction notice filed under a goods category can
never be excluded by that label.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import config
from matchrec import schema
from notices import db
from profiles import vocabulary


LOGGER = logging.getLogger(__name__)

MAPPING_PATH = Path(config.PROJECT_ROOT) / "matchrec" / "trade_mapping.json"

STATUS_MAPPED = vocabulary.TRADE_STATUS_MAPPED
STATUS_UNMAPPED = vocabulary.TRADE_STATUS_UNMAPPED
STATUS_NON_CONSTRUCTION = vocabulary.TRADE_STATUS_NON_CONSTRUCTION

#: Evidence fields, strongest first. Title and the source's own category are the
#: notice summarizing itself; description prose is weaker evidence.
EVIDENCE_PRECEDENCE = ("title", "category_raw", "description")
STRONG_EVIDENCE = ("title", "category_raw")


def _evidence_rank(source: str) -> int:
    try:
        return EVIDENCE_PRECEDENCE.index(source)
    except ValueError:
        return len(EVIDENCE_PRECEDENCE)


def _is_strong(source: str) -> bool:
    """Whether evidence came from the notice's own summary of itself."""
    return source in STRONG_EVIDENCE


class TradeMapping:
    """A compiled, validated view of the mapping file."""

    def __init__(self, payload: dict) -> None:
        self.version = str(payload.get("version") or "unversioned")
        self.families: dict[str, list[str]] = {
            str(name): [str(slug) for slug in slugs]
            for name, slugs in (payload.get("families") or {}).items()
        }
        self._family_of: dict[str, str] = {
            slug: family for family, slugs in self.families.items() for slug in slugs
        }
        self._rules = self._compile_rules(payload.get("rules") or [])
        non_construction = payload.get("non_construction") or {}
        self._non_construction_categories = {
            _fold(value) for value in non_construction.get("category_raw_exact") or []
        }
        self._non_construction_terms = _compile_terms(
            [
                *(non_construction.get("keywords_en") or []),
                *(non_construction.get("keywords_fr") or []),
            ]
        )
        markers = payload.get("construction_markers") or {}
        self._construction_categories = {
            _fold(value) for value in markers.get("category_normalized") or []
        }
        self._construction_keywords = {
            _fold(value) for value in markers.get("category_raw_keywords") or []
        }
        guard = payload.get("goods_category_guard") or {}
        self._goods_categories = {
            _fold(value) for value in guard.get("category_raw_exact") or []
        }
        self._goods_allowed_slugs = {
            str(value) for value in guard.get("allowed_slugs") or []
        }
        self._validate()

    def family_of(self, slug: str) -> str | None:
        """Return the family a slug belongs to, when it has one."""
        return self._family_of.get(slug)

    def shares_family(self, first: str, second: str) -> bool:
        """Whether two slugs sit in the same family."""
        family = self._family_of.get(first)
        return family is not None and family == self._family_of.get(second)

    def classify(self, notice: dict) -> dict:
        """Classify one notice into trade slugs, recording where each matched.

        Evidence location decides the primary slug. Title and the source's own
        category are the notice's summary of itself; a description can be pages of
        prose and rate tables, so a slug seen only there is ranked below any
        title-evidenced slug and is marked ``description`` so scoring can discount
        it. Without this, "Lead Structural Engineer" in a fee schedule was enough to
        file a heating-system replacement as bridge work.
        """
        fields = {
            "title": _fold(notice.get("title")),
            "category_raw": _fold(notice.get("category_raw")),
            "description": _fold(notice.get("description")),
        }
        combined = " \n ".join(value for value in fields.values() if value)

        matches: list[tuple[int, str, str, str]] = []
        for priority, slug, term, pattern in self._rules:
            # One search over the combined text rejects the vast majority cheaply;
            # only a hit pays for locating which field it came from.
            if pattern.search(combined) is None:
                continue
            source = next(
                (
                    name
                    for name in EVIDENCE_PRECEDENCE
                    if fields[name] and pattern.search(fields[name]) is not None
                ),
                "description",
            )
            matches.append((priority, slug, term, source))

        construction_marked = self.is_construction_marked(notice)
        if matches and not construction_marked:
            matches = self._apply_goods_guard(notice, matches)

        if matches:
            sources: dict[str, str] = {}
            for _, slug, _, source in matches:
                current = sources.get(slug)
                if current is None or _evidence_rank(source) < _evidence_rank(current):
                    sources[slug] = source
            matches.sort(
                key=lambda item: (
                    0 if _is_strong(sources[item[1]]) else 1,
                    item[0],
                    item[1],
                )
            )
            slugs: list[str] = []
            terms: list[str] = []
            for _, slug, term, _ in matches:
                if slug not in slugs:
                    slugs.append(slug)
                if term not in terms:
                    terms.append(term)
            return {
                "trade_slugs": slugs,
                "slug_sources": {slug: sources[slug] for slug in slugs},
                "mapping_status": STATUS_MAPPED,
                "matched_terms": terms[:12],
                "construction_marked": construction_marked,
            }

        if not construction_marked:
            reason = self._non_construction_reason(notice, combined)
            if reason is not None:
                return {
                    "trade_slugs": [],
                    "slug_sources": {},
                    "mapping_status": STATUS_NON_CONSTRUCTION,
                    "matched_terms": [reason],
                    "construction_marked": construction_marked,
                }

        return {
            "trade_slugs": [],
            "slug_sources": {},
            "mapping_status": STATUS_UNMAPPED,
            "matched_terms": [],
            "construction_marked": construction_marked,
        }

    def is_construction_marked(self, notice: dict) -> bool:
        """Whether the notice's own category marks it as construction work."""
        if _fold(notice.get("category_normalized")) in self._construction_categories:
            return True
        category_raw = _fold(notice.get("category_raw"))
        return any(keyword in category_raw for keyword in self._construction_keywords)

    def _apply_goods_guard(
        self, notice: dict, matches: list[tuple[int, str, str, str]]
    ) -> list[tuple[int, str, str, str]]:
        """Drop trade matches that a goods procurement cannot plausibly be.

        A notice the buyer filed as goods can legitimately be granular supply or
        equipment rental. A "bridge" keyword on a goods notice is bridging equipment,
        not bridge construction — the mistake this guard exists to prevent.
        """
        if _fold(notice.get("category_raw")) not in self._goods_categories:
            return matches
        kept = [match for match in matches if match[1] in self._goods_allowed_slugs]
        if len(kept) != len(matches):
            LOGGER.debug(
                "Goods guard dropped %d match(es) for %r",
                len(matches) - len(kept),
                str(notice.get("title"))[:60],
            )
        return kept

    def _non_construction_reason(self, notice: dict, haystack: str) -> str | None:
        category_raw = _fold(notice.get("category_raw"))
        if category_raw and category_raw in self._non_construction_categories:
            return f"category:{notice.get('category_raw')}"
        for term, pattern in self._non_construction_terms:
            if pattern.search(haystack) is not None:
                return f"keyword:{term}"
        return None

    def _compile_rules(
        self, rules: Iterable[Any]
    ) -> list[tuple[int, str, str, re.Pattern[str]]]:
        compiled: list[tuple[int, str, str, re.Pattern[str]]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            slug = str(rule.get("slug") or "")
            priority = int(rule.get("priority", 100))
            terms = [
                *(rule.get("keywords_en") or []),
                *(rule.get("keywords_fr") or []),
            ]
            for term, pattern in _compile_terms(terms):
                compiled.append((priority, slug, term, pattern))
        return compiled

    def _validate(self) -> None:
        problems: list[str] = []
        rule_slugs = {slug for _, slug, _, _ in self._rules}
        unknown = sorted(rule_slugs - set(vocabulary.TRADE_SLUGS))
        if unknown:
            problems.append(f"rules reference unknown trade slugs: {unknown}")
        family_slugs = set(self._family_of)
        unknown_family = sorted(family_slugs - set(vocabulary.TRADE_SLUGS))
        if unknown_family:
            problems.append(f"families reference unknown trade slugs: {unknown_family}")
        missing_rules = sorted(set(vocabulary.TRADE_SLUGS) - rule_slugs)
        if missing_rules:
            # Not fatal: a slug can be firm-only until the mapping catches up.
            LOGGER.warning(
                "Trade slugs with no mapping rule yet: %s", ", ".join(missing_rules)
            )
        for slug in sorted(rule_slugs):
            if slug not in self._family_of:
                LOGGER.warning("Trade slug %s belongs to no family", slug)
        if problems:
            raise ValueError("Invalid trade mapping: " + "; ".join(problems))


def load_mapping(path: Path | str | None = None) -> TradeMapping:
    """Load and validate the mapping file."""
    source = Path(path) if path else MAPPING_PATH
    with source.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    mapping = TradeMapping(payload)
    LOGGER.info(
        "Loaded trade mapping %s: %d rules across %d slugs",
        mapping.version,
        len(mapping._rules),
        len({slug for _, slug, _, _ in mapping._rules}),
    )
    return mapping


def map_notices(
    connection: sqlite3.Connection,
    mapping: TradeMapping | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Classify every notice and store the result in notice_trades."""
    mapping = mapping or load_mapping()
    timestamp = now or db.utc_timestamp()
    rows = connection.execute(
        "SELECT id, title, description, category_raw, category_normalized FROM tenders"
    ).fetchall()

    prepared: list[dict] = []
    status_counts: dict[str, int] = {}
    construction_total = 0
    construction_mapped = 0
    for row in rows:
        notice = dict(row)
        result = mapping.classify(notice)
        status_counts[result["mapping_status"]] = (
            status_counts.get(result["mapping_status"], 0) + 1
        )
        if mapping.is_construction_marked(notice):
            construction_total += 1
            if result["mapping_status"] == STATUS_MAPPED:
                construction_mapped += 1
        prepared.append(
            {
                "tender_id": int(row["id"]),
                "trade_slugs": schema.dumps(result["trade_slugs"]),
                "slug_sources": schema.dumps(result["slug_sources"]),
                "mapping_status": result["mapping_status"],
                "matched_terms": schema.dumps(result["matched_terms"]),
                "construction_marked": 1 if result["construction_marked"] else 0,
                "mapping_version": mapping.version,
            }
        )

    tally = schema.upsert_rows(
        connection,
        "notice_trades",
        ("tender_id",),
        schema.TRADE_CONTENT_COLUMNS,
        "updated_at",
        prepared,
        timestamp,
    )
    coverage = (
        100.0 * construction_mapped / construction_total if construction_total else 0.0
    )
    LOGGER.info(
        "Mapped %d notices (%s); construction coverage %.1f%% (%d of %d)",
        len(prepared),
        ", ".join(f"{key} {value}" for key, value in sorted(status_counts.items())),
        coverage,
        construction_mapped,
        construction_total,
    )
    return {
        "mapping_version": mapping.version,
        "notices": len(prepared),
        "status_counts": status_counts,
        "construction_total": construction_total,
        "construction_mapped": construction_mapped,
        "construction_coverage_pct": round(coverage, 1),
        **tally,
    }


def unmapped_categories(
    connection: sqlite3.Connection, limit: int = 20, construction_only: bool = True
) -> list[dict]:
    """Return the most common raw categories that produced no trade slug."""
    clause = (
        "AND t.category_normalized = 'construction'" if construction_only else ""
    )
    rows = connection.execute(
        "SELECT COALESCE(t.category_raw, '(none)') AS category_raw, t.source, "
        "       COUNT(*) AS total, MIN(t.title) AS example "
        "FROM tenders t JOIN notice_trades nt ON nt.tender_id = t.id "
        f"WHERE nt.mapping_status != 'mapped' {clause} "
        "GROUP BY category_raw, t.source ORDER BY total DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def unmapped_examples(
    connection: sqlite3.Connection, limit: int = 20, construction_only: bool = True
) -> list[dict]:
    """Return example titles that produced no trade slug, for extending the mapping."""
    clause = (
        "AND t.category_normalized = 'construction'" if construction_only else ""
    )
    rows = connection.execute(
        "SELECT t.source, t.category_raw, t.title FROM tenders t "
        "JOIN notice_trades nt ON nt.tender_id = t.id "
        f"WHERE nt.mapping_status != 'mapped' {clause} "
        "ORDER BY t.closing_date DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def _compile_terms(terms: Iterable[Any]) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for raw in terms:
        term = _fold(raw)
        if not term:
            continue
        # Word-boundary anchored so "road" cannot match "broad", while multi-word
        # phrases stay tolerant of the whitespace variation real titles contain.
        # The final word takes an optional plural "s" so one entry covers
        # "travaux électrique(s)" and "égout(s)" without doubling every list.
        parts = [re.escape(part) for part in term.split()]
        parts[-1] = parts[-1] + ("" if parts[-1].endswith("s") else "s?")
        pattern = r"\s+".join(parts)
        compiled.append((term, re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])")))
    return compiled


def _fold(value: Any) -> str:
    """Casefold, strip accents, and collapse whitespace for robust matching."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    text = text.replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Map notices onto trade slugs")
    parser.add_argument("--db", default=None)
    parser.add_argument("--mapping", default=None)
    parser.add_argument(
        "--show-unmapped",
        type=int,
        default=0,
        help="print the top N unmapped raw categories",
    )
    args = parser.parse_args()

    connection = schema.connect(args.db)
    try:
        mapping = load_mapping(args.mapping)
        result = map_notices(connection, mapping)
        print(
            f"mapping {result['mapping_version']}: {result['notices']} notices "
            f"(inserted {result['inserted']}, updated {result['updated']}, "
            f"unchanged {result['unchanged']})"
        )
        for status, count in sorted(result["status_counts"].items()):
            print(f"  {status:<18} {count}")
        print(
            f"\nconstruction coverage: {result['construction_coverage_pct']}% "
            f"({result['construction_mapped']} of {result['construction_total']})"
        )
        if args.show_unmapped:
            print(f"\ntop {args.show_unmapped} unmapped raw categories:")
            for row in unmapped_categories(connection, args.show_unmapped):
                print(
                    f"  {row['total']:>6}  [{row['source']}] {row['category_raw']} "
                    f"— e.g. {str(row['example'])[:70]}"
                )
    finally:
        connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
