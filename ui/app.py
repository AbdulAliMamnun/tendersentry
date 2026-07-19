"""Streamlit demo UI for TenderSentry's on-disk pipeline results."""

from __future__ import annotations

import html
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import streamlit as st


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config  # noqa: E402


LOGGER = logging.getLogger(__name__)
TENDERS_DIR = REPO_ROOT / config.DATA_DIR
PROFILE_PATH = REPO_ROOT / "data" / "profile.json"
NOTICES_PATH = Path(config.NOTICES_PATH)

VERDICT_LABELS = {
    "bid": "BID",
    "review": "REVIEW",
    "no_bid": "DON'T BID",
    "not_analyzed": "NOT ANALYZED",
}


def _read_json(path: Path, default: Any) -> Any:
    """Read JSON safely, returning the supplied default for missing/invalid files."""
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return default


def _data_snapshot() -> tuple[tuple[str, int, int], ...]:
    """Return file and directory mtimes used as the cache key."""
    paths = [NOTICES_PATH, PROFILE_PATH, TENDERS_DIR]
    if TENDERS_DIR.is_dir():
        for tender_dir in sorted(TENDERS_DIR.iterdir()):
            if not tender_dir.is_dir():
                continue
            paths.append(tender_dir)
            paths.extend(
                tender_dir / filename
                for filename in (
                    "requirements.json",
                    "decision.json",
                    "dropped.json",
                )
            )
    snapshot: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
            snapshot.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            snapshot.append((str(path), -1, -1))
    return tuple(snapshot)


@st.cache_data(show_spinner=False)
def load_all_data(snapshot: tuple[tuple[str, int, int], ...]) -> dict:
    """Load all UI JSON once for a snapshot of the underlying file mtimes."""
    del snapshot
    notices_value = _read_json(NOTICES_PATH, [])
    notices = notices_value if isinstance(notices_value, list) else []
    notices_by_id = {
        str(item.get("tender_id", "")): item
        for item in notices
        if isinstance(item, dict) and item.get("tender_id")
    }
    profile_value = _read_json(PROFILE_PATH, {})
    profile = profile_value if isinstance(profile_value, dict) else {}

    tenders: list[dict] = []
    tender_dirs = (
        [path for path in sorted(TENDERS_DIR.iterdir()) if path.is_dir()]
        if TENDERS_DIR.is_dir()
        else []
    )
    for tender_dir in tender_dirs:
        tender_id = tender_dir.name
        requirements_value = _read_json(tender_dir / "requirements.json", [])
        requirements = (
            requirements_value if isinstance(requirements_value, list) else []
        )
        decision_value = _read_json(tender_dir / "decision.json", {})
        decision = decision_value if isinstance(decision_value, dict) else {}
        dropped_value = _read_json(tender_dir / "dropped.json", [])
        dropped = dropped_value if isinstance(dropped_value, list) else []
        notice = notices_by_id.get(tender_id, {})
        tenders.append(
            {
                "tender_id": tender_id,
                "title": str(notice.get("title") or tender_id),
                "closing_date": notice.get("closing_date"),
                "requirements": requirements,
                "decision": decision,
                "dropped": dropped,
            }
        )
    return {"profile": profile, "tenders": tenders}


def _valid_verdict(tender: dict) -> str:
    verdict = str(tender.get("decision", {}).get("verdict", ""))
    return verdict if verdict in {"bid", "review", "no_bid"} else "not_analyzed"


def _parse_closing(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _closing_sort_key(tender: dict) -> tuple[int, str]:
    closing = _parse_closing(tender.get("closing_date"))
    return (0, closing.isoformat()) if closing else (1, tender["tender_id"])


def _closing_text(value: Any) -> tuple[str, int | None]:
    closing = _parse_closing(value)
    if closing is None:
        return "Closing date unavailable", None
    days = (closing.date() - date.today()).days
    text = closing.strftime("%b %-d, %Y at %-I:%M %p")
    if 0 <= days < 14:
        text += f" · closes in {days} day{'s' if days != 1 else ''}"
    return text, days


def _render_styles() -> None:
    st.markdown(
        """
<style>
html, body, [class*="st-"] { font-size: 14px; }
.block-container { max-width: 1180px; padding-top: 1rem; padding-bottom: 2rem; }
[data-testid="stVerticalBlock"] { gap: 0.55rem; }
[data-testid="stCaptionContainer"], [data-testid="stMetricLabel"] { font-size: 14px; }
.stButton button, [data-testid="stPopover"] button {
  color: #111827; border-color: #64748b; background: #fff;
}
div[class*="st-key-tender-"] { border-radius: 0; padding: 0.65rem 0.8rem; }
div[class*="st-key-tender-bid-"] { border-left: 5px solid #3f6212 !important; }
div[class*="st-key-tender-review-"] { border-left: 5px solid #a16207 !important; }
div[class*="st-key-tender-no_bid-"] { border-left: 5px solid #b91c1c !important; }
div[class*="st-key-tender-not_analyzed-"] { border-left: 5px solid #64748b !important; }
div[class*="st-key-requirement-"] {
  border: 0; border-top: 1px solid #d1d5db; border-radius: 0; padding: 0.45rem 0;
}
.estimator-status {
  display: inline-block; min-width: 5ch;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700;
}
.estimator-fail, .estimator-urgent { color: #b91c1c; font-weight: 700; }
.estimator-letterhead {
  border-bottom: 1px solid #111827; padding-bottom: 0.35rem; margin-bottom: 0.7rem;
}
.estimator-firm { text-align: right; white-space: nowrap; }
.estimator-product { font-size: 14px; }
@media print {
  [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"],
  [class*="st-key-no-print"], button { display: none !important; }
  html, body, [class*="st-"] { color: #000 !important; background: #fff !important; }
  .block-container { max-width: none; padding: 0.25in 0.35in; }
  div[class*="st-key-requirement-"] { break-inside: avoid; page-break-inside: avoid; }
  details > summary { display: none !important; }
  details:not([open]) > :not(summary) { display: block !important; }
  details [data-testid="stExpanderDetails"] { display: block !important; }
  .estimator-letterhead { margin-bottom: 0.15in; }
}
</style>
""",
        unsafe_allow_html=True,
    )


def _render_letterhead(profile: dict) -> None:
    certifications = " · ".join(str(item) for item in profile.get("certifications", []))
    bonding = profile.get("bonding_capacity_cad")
    bonding_text = (
        f"Bonding ${bonding:,.0f}"
        if isinstance(bonding, (int, float))
        else "Bonding unavailable"
    )
    regions = " · ".join(str(item) for item in profile.get("regions", []))
    firm_parts = [
        str(profile.get("firm_name") or "Firm profile unavailable"),
        certifications,
        bonding_text,
        regions,
    ]
    firm_line = " — " + " · ".join(part for part in firm_parts[1:] if part)
    with st.container(key="letterhead"):
        left, right = st.columns([1, 4])
        with left:
            st.markdown(
                f'<span class="estimator-product">{html.escape(config.PRODUCT_NAME)}</span>',
                unsafe_allow_html=True,
            )
        with right:
            st.markdown(
                f'<div class="estimator-firm">{html.escape(firm_parts[0] + firm_line)}</div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div class="estimator-letterhead"></div>', unsafe_allow_html=True)


def _status_text(judgment: dict | None) -> tuple[str, str]:
    verdict = str((judgment or {}).get("verdict", "uncertain"))
    if verdict == "satisfied":
        return "PASS", ""
    if verdict == "not_satisfied":
        return "FAIL", " estimator-fail"
    return "CHECK", ""


def _requirements_by_id(tender: dict) -> dict[str, dict]:
    return {
        str(requirement.get("id", "")): requirement
        for requirement in tender.get("requirements", [])
        if isinstance(requirement, dict)
    }


def _truncate(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _closing_board_text(value: Any) -> tuple[str, bool]:
    closing = _parse_closing(value)
    if closing is None:
        return "Closing unavailable", False
    days = (closing.date() - date.today()).days
    day_text = f"{days} day{'s' if days != 1 else ''}" if days >= 0 else "closed"
    return f"Closes {closing.strftime('%a %b %-d')} — {day_text}", 0 <= days < 5


def _counts_text(decision: dict) -> str:
    counts = decision.get("counts", {}) if isinstance(decision, dict) else {}
    return (
        f'Mandatory items: {counts.get("mandatory", 0)} · '
        f'Pass {counts.get("passed", 0)} · Fail {counts.get("failed", 0)} · '
        f'Unresolved {counts.get("uncertain", 0)}'
    )


def _bid_security_text(tender: dict) -> str:
    requirement = next(
        (
            item
            for item in tender.get("requirements", [])
            if isinstance(item, dict)
            and item.get("phase") == "bid_phase_mandatory"
            and item.get("category") == "bid_security"
        ),
        None,
    )
    if requirement is None:
        return "Bid security: not identified"
    return "Bid security: " + _truncate(
        str(requirement.get("requirement_text") or "requirement identified"), 85
    )


def _submission_text(tender: dict) -> str:
    requirements = [
        item
        for item in tender.get("requirements", [])
        if isinstance(item, dict)
        and item.get("phase") == "bid_phase_mandatory"
        and item.get("category") == "submission"
    ]
    methods: list[str] = []
    for requirement in requirements:
        if requirement.get("check_field") != "submission_method":
            continue
        value = str(requirement.get("check_value") or "").strip()
        if value and value not in methods:
            methods.append(value)
    if methods:
        return "Submission: " + " / ".join(methods)
    if requirements:
        return "Submission: " + _truncate(
            str(requirements[0].get("requirement_text") or "see tender documents"),
            80,
        )
    return "Submission: not identified"


def _render_decision_items(
    heading: str, ids: list[Any], requirements_by_id: dict[str, dict]
) -> None:
    if not ids:
        return
    st.markdown(f"**{heading}**")
    for requirement_id in ids:
        requirement = requirements_by_id.get(str(requirement_id), {})
        st.write(requirement.get("requirement_text") or str(requirement_id))
        quote = requirement.get("verbatim_quote")
        if quote:
            st.caption(f'{quote} — p.{requirement.get("page_number", "?")}')


def _select_tender(tender_id: str) -> None:
    st.session_state.selected_tender_id = tender_id
    st.session_state.view = "Bid checklist"


def _back_to_feed() -> None:
    st.session_state.view = "Bid board"


def _render_tender_card(tender: dict, analyzed: bool = True) -> None:
    tender_id = tender["tender_id"]
    decision = tender.get("decision", {})
    verdict = _valid_verdict(tender)
    requirements_by_id = _requirements_by_id(tender)

    safe_id = tender_id.replace(".", "-")
    with st.container(border=True, key=f"tender-{verdict}-{safe_id}"):
        closing_column, title_column, verdict_column = st.columns([2.1, 5.9, 1.35])
        closing_text, urgent = _closing_board_text(tender.get("closing_date"))
        with closing_column:
            css_class = "estimator-urgent" if urgent else ""
            st.markdown(
                f'<span class="{css_class}"><strong>{html.escape(closing_text)}</strong></span>',
                unsafe_allow_html=True,
            )
        with title_column:
            st.markdown(f'**{html.escape(str(tender["title"]))}**')
            st.caption(tender_id)
        with verdict_column:
            verdict_class = "estimator-fail" if verdict == "no_bid" else ""
            st.markdown(
                f'<span class="{verdict_class}"><strong>{VERDICT_LABELS[verdict]}</strong></span>',
                unsafe_allow_html=True,
            )

        line_two = f"{_bid_security_text(tender)} · {_submission_text(tender)}"
        blocker: dict = {}
        if analyzed and verdict == "no_bid":
            blocker_ids = decision.get("blockers", [])
            blocker = requirements_by_id.get(str(blocker_ids[0]), {}) if blocker_ids else {}
            line_two += (
                " · Blocked — "
                f'{blocker.get("requirement_text", "blocking requirement unavailable")} '
                f'(p.{blocker.get("page_number", "?")}, '
                f'{blocker.get("source_file", "source unavailable")})'
            )
        st.write(line_two)
        if blocker.get("verbatim_quote"):
            st.caption(
                f'"{blocker["verbatim_quote"]}" — p.{blocker.get("page_number", "?")}, '
                f'{blocker.get("source_file", "source unavailable")}'
            )

        count_column, why_column, checklist_column = st.columns([7.5, 0.8, 1.15])
        with count_column:
            st.write(_counts_text(decision) if analyzed else "Mandatory items: not analyzed")
        with why_column:
            with st.popover("Why"):
                if analyzed:
                    st.write(decision.get("rationale") or "No decision note available.")
                    _render_decision_items(
                        "Blockers", decision.get("blockers", []), requirements_by_id
                    )
                    _render_decision_items(
                        "Items to check",
                        decision.get("open_questions", []),
                        requirements_by_id,
                    )
                else:
                    st.write("This tender has not been analyzed.")
        with checklist_column:
            st.button(
                "Checklist",
                key=f"checklist-{tender_id}",
                on_click=_select_tender,
                args=(tender_id,),
            )


def _render_feed(tenders: list[dict]) -> None:
    st.title("Bid board")
    closing_within_seven = 0
    for tender in tenders:
        closing = _parse_closing(tender.get("closing_date"))
        if closing is not None and 0 <= (closing.date() - date.today()).days <= 7:
            closing_within_seven += 1
    metric_columns = st.columns(3)
    metric_columns[0].metric("Closing within 7 days", closing_within_seven)
    metric_columns[1].metric(
        "Ready to bid", sum(_valid_verdict(tender) == "bid" for tender in tenders)
    )
    metric_columns[2].metric(
        "Blocked", sum(_valid_verdict(tender) == "no_bid" for tender in tenders)
    )
    groups = [
        ("bid", "Recommended to bid"),
        ("review", "Review before deciding"),
        ("no_bid", "Do not bid"),
    ]
    for verdict, label in groups:
        grouped = sorted(
            [tender for tender in tenders if _valid_verdict(tender) == verdict],
            key=_closing_sort_key,
        )
        st.subheader(f"{label} ({len(grouped)})")
        for tender in grouped:
            _render_tender_card(tender)

    not_analyzed = sorted(
        [
            tender
            for tender in tenders
            if _valid_verdict(tender) == "not_analyzed"
        ],
        key=_closing_sort_key,
    )
    with st.expander(f"Not analyzed ({len(not_analyzed)})", expanded=False):
        for tender in not_analyzed:
            _render_tender_card(tender, analyzed=False)


def _render_requirement(
    requirement: dict,
    judgment: dict | None,
    show_status: bool,
) -> None:
    requirement_id = str(requirement.get("id", "unknown")).replace(".", "-")
    with st.container(key=f"requirement-{requirement_id}"):
        if show_status:
            status_column, content_column = st.columns([1, 11])
            status, status_class = _status_text(judgment)
            with status_column:
                st.markdown(
                    f'<span class="estimator-status{status_class}">{status}</span>',
                    unsafe_allow_html=True,
                )
        else:
            content_column = st.container()
        with content_column:
            st.write(requirement.get("requirement_text") or "Requirement text unavailable")
            quote = requirement.get("verbatim_quote")
            if quote:
                st.caption(
                    f'"{quote}" — p.{requirement.get("page_number", "?")}, '
                    f'{requirement.get("source_file", "source unavailable")}'
                )
            if requirement.get("verification_status") == "page_repaired":
                original_page = requirement.get("original_page_number")
                correction = (
                    f"[page corrected from {original_page}]"
                    if original_page is not None
                    else "[page corrected]"
                )
                st.caption(correction)
            if requirement.get("machine_checkable") is True:
                st.caption(
                    "Rule check: "
                    f'{requirement.get("check_field")} '
                    f'{requirement.get("check_operator")} '
                    f'{requirement.get("check_value")}'
                )


def _render_requirement_groups(
    requirements: list[dict], judgments_by_id: dict[str, dict], show_status: bool
) -> None:
    sections = [
        ("Submission rules", {"submission"}),
        ("Bid security", {"bid_security"}),
        ("Certifications", {"certification"}),
        ("Insurance", {"insurance"}),
        ("Eligibility", {"eligibility"}),
        ("Other", {"evaluation", "other_mandatory"}),
    ]
    known_categories = set().union(*(categories for _, categories in sections))
    for heading, categories in sections:
        section_items = [
            requirement
            for requirement in requirements
            if str(requirement.get("category", "other_mandatory")) in categories
            or (
                heading == "Other"
                and str(requirement.get("category", "other_mandatory"))
                not in known_categories
            )
        ]
        if not section_items:
            if show_status:
                st.markdown(f"### {heading}")
                st.caption("None identified in the bid documents.")
            continue
        st.markdown(f"### {heading}")
        for requirement in section_items:
            judgment = judgments_by_id.get(str(requirement.get("id", "")))
            _render_requirement(requirement, judgment, show_status)


def _render_tender_cover(tender: dict) -> None:
    closing_text, _ = _closing_text(tender.get("closing_date"))
    st.title(tender["title"])
    cover = st.columns([1, 3])
    with cover[0]:
        st.markdown("**Solicitation number**")
        st.markdown("**Closing**")
        st.markdown("**Submission method**")
    with cover[1]:
        st.write(tender["tender_id"])
        st.write(closing_text)
        st.write(_submission_text(tender).removeprefix("Submission: "))


def _render_checklist(tender: dict | None) -> None:
    if tender is None:
        st.title("Bid checklist")
        st.info("Select a tender from the bid board to view its checklist.")
        return

    with st.container(key="no-print-back"):
        st.button("Back to bid board", on_click=_back_to_feed)
    decision = tender.get("decision", {})
    verdict = _valid_verdict(tender)
    _render_tender_cover(tender)
    verdict_class = "estimator-fail" if verdict == "no_bid" else ""
    st.markdown(
        f'<span class="{verdict_class}"><strong>'
        f'Bid decision: {VERDICT_LABELS[verdict]}</strong></span>',
        unsafe_allow_html=True,
    )

    requirements = [
        item for item in tender.get("requirements", []) if isinstance(item, dict)
    ]
    bid_phase = [
        item for item in requirements if item.get("phase") == "bid_phase_mandatory"
    ]
    contract_conditions = [
        item for item in requirements if item.get("phase") == "contract_condition"
    ]
    judgments_by_id = {
        str(item.get("id", "")): item
        for item in decision.get("judgments", [])
        if isinstance(item, dict)
    }

    st.header(f"Bid checklist ({len(bid_phase)} mandatory items)")
    _render_requirement_groups(bid_phase, judgments_by_id, show_status=True)

    with st.expander(
        f"Conditions after award ({len(contract_conditions)})",
        expanded=False,
    ):
        _render_requirement_groups(
            contract_conditions, judgments_by_id, show_status=False
        )

    dropped_count = len(tender.get("dropped", []))
    st.markdown("---")
    st.write(
        "All quotes above were machine-verified to exist at the cited page of the "
        f"source document. {dropped_count} unverifiable extractions were discarded."
    )


def _render_roadmap() -> None:
    st.title("Roadmap")
    st.markdown(
        """
- **Weekly email digests** — prioritized tender opportunities delivered on schedule.
- **All-portal coverage** — MERX, bids&tenders, and provincial procurement portals.
- **One-click document capture extension** — save tender packages directly into the workflow.
- **Addenda conflict detection** — flag requirement changes and contradictions automatically.
- **Exportable checklist PDF** — share a portable bid checklist with the estimating team.
"""
    )


def _render_sidebar(profile: dict, tenders: list[dict]) -> None:
    del profile
    st.sidebar.caption("Sources: CanadaBuys open data + uploaded packages")

    counts = {
        verdict: sum(_valid_verdict(tender) == verdict for tender in tenders)
        for verdict in ("bid", "review", "no_bid", "not_analyzed")
    }
    st.sidebar.markdown("**Bid board count**")
    st.sidebar.caption(
        f'Ready {counts["bid"]} · Review {counts["review"]} · '
        f'Blocked {counts["no_bid"]} · Not analyzed {counts["not_analyzed"]}'
    )

    if "view" not in st.session_state:
        st.session_state.view = "Bid board"
    st.sidebar.radio(
        "Navigate",
        ["Bid board", "Bid checklist", "Roadmap"],
        key="view",
    )


def main() -> None:
    """Render the read-only TenderSentry demo application."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    st.set_page_config(page_title=config.PRODUCT_NAME, layout="wide")
    _render_styles()
    data = load_all_data(_data_snapshot())
    tenders = data["tenders"]
    _render_sidebar(data["profile"], tenders)
    _render_letterhead(data["profile"])

    view = st.session_state.view
    if view == "Roadmap":
        _render_roadmap()
        return
    if view == "Bid checklist":
        selected_id = st.session_state.get("selected_tender_id")
        selected = next(
            (tender for tender in tenders if tender["tender_id"] == selected_id),
            None,
        )
        _render_checklist(selected)
        return
    _render_feed(tenders)


if __name__ == "__main__":
    main()
