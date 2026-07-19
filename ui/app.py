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

CATEGORY_ORDER = [
    "submission",
    "bid_security",
    "certification",
    "insurance",
    "eligibility",
    "evaluation",
    "other_mandatory",
]

CATEGORY_LABELS = {
    "submission": "Submission",
    "bid_security": "Bid security",
    "certification": "Certification",
    "insurance": "Insurance",
    "eligibility": "Eligibility",
    "evaluation": "Evaluation",
    "other_mandatory": "Other mandatory",
}

VERDICT_LABELS = {
    "bid": "Bid",
    "review": "Review",
    "no_bid": "Don't bid",
    "not_analyzed": "Not yet analyzed",
}

VERDICT_COLORS = {
    "bid": ("#166534", "#dcfce7"),
    "review": ("#92400e", "#fef3c7"),
    "no_bid": ("#991b1b", "#fee2e2"),
    "not_analyzed": ("#475569", "#e2e8f0"),
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


def _badge(verdict: str) -> str:
    foreground, background = VERDICT_COLORS[verdict]
    label = VERDICT_LABELS[verdict]
    return (
        f'<span style="display:inline-block;padding:0.2rem 0.55rem;border-radius:999px;'
        f'font-weight:600;color:{foreground};background:{background};">{label}</span>'
    )


def _status_icon(judgment: dict | None) -> str:
    verdict = str((judgment or {}).get("verdict", "uncertain"))
    if verdict == "satisfied":
        return '<span style="color:#15803d;font-weight:700;">✓</span>'
    if verdict == "not_satisfied":
        return '<span style="color:#b91c1c;font-weight:700;">✗</span>'
    return '<span style="color:#b45309;font-weight:700;">?</span>'


def _requirements_by_id(tender: dict) -> dict[str, dict]:
    return {
        str(requirement.get("id", "")): requirement
        for requirement in tender.get("requirements", [])
        if isinstance(requirement, dict)
    }


def _truncate(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _render_closing(value: Any) -> None:
    text, days = _closing_text(value)
    if days is not None and 0 <= days < 5:
        st.markdown(f'<span style="color:#b91c1c;">{html.escape(text)}</span>', unsafe_allow_html=True)
    else:
        st.caption(text)


def _render_counts(decision: dict) -> None:
    counts = decision.get("counts", {}) if isinstance(decision, dict) else {}
    columns = st.columns(4)
    for column, label, key in zip(
        columns,
        ("Mandatory", "Passed", "Failed", "Uncertain"),
        ("mandatory", "passed", "failed", "uncertain"),
    ):
        with column:
            st.caption(label)
            st.write(str(counts.get(key, 0)))


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
    st.session_state.view = "Compliance checklist"


def _back_to_feed() -> None:
    st.session_state.view = "Triage feed"


def _render_tender_card(tender: dict, analyzed: bool = True) -> None:
    tender_id = tender["tender_id"]
    decision = tender.get("decision", {})
    verdict = _valid_verdict(tender)
    requirements_by_id = _requirements_by_id(tender)

    with st.container(border=True):
        title_column, badge_column = st.columns([5, 1])
        with title_column:
            st.subheader(tender["title"])
            st.caption(tender_id)
            _render_closing(tender.get("closing_date"))
        with badge_column:
            st.markdown(_badge(verdict), unsafe_allow_html=True)

        if analyzed:
            if verdict == "no_bid":
                blocker_ids = decision.get("blockers", [])
                blocker = requirements_by_id.get(
                    str(blocker_ids[0]), {}
                ) if blocker_ids else {}
                st.write(
                    "Blocked: "
                    f'{blocker.get("requirement_text", "Blocking requirement unavailable")} '
                    f'— p.{blocker.get("page_number", "?")}, '
                    f'{blocker.get("source_file", "source unavailable")}'
                )
                if blocker.get("verbatim_quote"):
                    st.caption(
                        f'{blocker["verbatim_quote"]} — '
                        f'p.{blocker.get("page_number", "?")}, '
                        f'{blocker.get("source_file", "source unavailable")}'
                    )
            elif verdict == "review":
                open_ids = decision.get("open_questions", [])
                first = requirements_by_id.get(str(open_ids[0]), {}) if open_ids else {}
                st.write(
                    f'{len(open_ids)} open questions — top: '
                    f'{_truncate(str(first.get("requirement_text", "Unavailable")))}'
                )
            elif verdict == "bid":
                mandatory = decision.get("counts", {}).get("mandatory", 0)
                st.write(f"All {mandatory} mandatory requirements pass")

            _render_counts(decision)
            with st.expander("Why?"):
                st.write(decision.get("rationale") or "No rationale available.")
                _render_decision_items(
                    "Blockers", decision.get("blockers", []), requirements_by_id
                )
                _render_decision_items(
                    "Open questions",
                    decision.get("open_questions", []),
                    requirements_by_id,
                )
        else:
            st.write("Not yet analyzed")

        st.button(
            "Full compliance checklist",
            key=f"checklist-{tender_id}",
            on_click=_select_tender,
            args=(tender_id,),
        )


def _render_feed(tenders: list[dict]) -> None:
    st.title("Tender triage")
    groups = [
        ("bid", "Bid"),
        ("review", "Review"),
        ("no_bid", "Don't bid"),
    ]
    for verdict, label in groups:
        grouped = sorted(
            [tender for tender in tenders if _valid_verdict(tender) == verdict],
            key=_closing_sort_key,
        )
        st.header(f"{label} ({len(grouped)})")
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
    with st.expander(f"Not yet analyzed ({len(not_analyzed)})", expanded=False):
        for tender in not_analyzed:
            _render_tender_card(tender, analyzed=False)


def _ordered_categories(requirements: list[dict]) -> list[str]:
    present = {
        str(requirement.get("category", "other_mandatory"))
        for requirement in requirements
    }
    return [item for item in CATEGORY_ORDER if item in present] + sorted(
        present - set(CATEGORY_ORDER)
    )


def _render_requirement(
    requirement: dict,
    judgment: dict | None,
    show_status: bool,
) -> None:
    with st.container(border=True):
        if show_status:
            icon_column, content_column = st.columns([1, 24])
            with icon_column:
                st.markdown(_status_icon(judgment), unsafe_allow_html=True)
        else:
            content_column = st.container()
        with content_column:
            st.write(requirement.get("requirement_text") or "Requirement text unavailable")
            quote = requirement.get("verbatim_quote")
            if quote:
                st.caption(
                    f'{quote} — p.{requirement.get("page_number", "?")}, '
                    f'{requirement.get("source_file", "source unavailable")}'
                )
            if requirement.get("verification_status") == "page_repaired":
                st.markdown(
                    '<span style="font-size:0.75rem;color:#475569;background:#e2e8f0;'
                    'padding:0.1rem 0.4rem;border-radius:999px;">page corrected</span>',
                    unsafe_allow_html=True,
                )
            if requirement.get("machine_checkable") is True:
                st.caption(
                    "auto-checked: "
                    f'{requirement.get("check_field")} '
                    f'{requirement.get("check_operator")} '
                    f'{requirement.get("check_value")}'
                )


def _render_requirement_groups(
    requirements: list[dict], judgments_by_id: dict[str, dict], show_status: bool
) -> None:
    for category in _ordered_categories(requirements):
        st.markdown(f"### {CATEGORY_LABELS.get(category, category.replace('_', ' ').title())}")
        for requirement in requirements:
            if str(requirement.get("category", "other_mandatory")) != category:
                continue
            judgment = judgments_by_id.get(str(requirement.get("id", "")))
            _render_requirement(requirement, judgment, show_status)


def _render_checklist(tender: dict | None) -> None:
    if tender is None:
        st.title("Compliance checklist")
        st.info("Select a tender from the triage feed to view its checklist.")
        return

    st.button("← Back to triage", on_click=_back_to_feed)
    decision = tender.get("decision", {})
    verdict = _valid_verdict(tender)
    title_column, badge_column = st.columns([5, 1])
    with title_column:
        st.title(tender["title"])
        _render_closing(tender.get("closing_date"))
    with badge_column:
        st.markdown(_badge(verdict), unsafe_allow_html=True)

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

    st.header(f"Bid submission requirements ({len(bid_phase)})")
    _render_requirement_groups(bid_phase, judgments_by_id, show_status=True)

    with st.expander(
        f"Contract conditions if awarded ({len(contract_conditions)})",
        expanded=False,
    ):
        _render_requirement_groups(
            contract_conditions, judgments_by_id, show_status=False
        )

    dropped_count = len(tender.get("dropped", []))
    st.markdown("---")
    st.write(
        "Every requirement above carries a verbatim quote verified to exist at "
        f"the cited page. {dropped_count} unverifiable extraction(s) were dropped "
        "and are not shown."
    )


def _render_roadmap() -> None:
    st.title("Roadmap")
    st.markdown(
        """
- **Weekly email digests** — prioritized tender opportunities delivered on schedule.
- **All-portal coverage** — MERX, bids&tenders, and provincial procurement portals.
- **One-click document capture extension** — save tender packages directly into the workflow.
- **Addenda conflict detection** — flag requirement changes and contradictions automatically.
- **Exportable checklist PDF** — share a portable compliance checklist with the bid team.
"""
    )


def _render_sidebar(profile: dict, tenders: list[dict]) -> None:
    st.sidebar.title(config.PRODUCT_NAME)
    with st.sidebar.container(border=True):
        st.subheader(str(profile.get("firm_name") or "Firm profile unavailable"))
        certifications = profile.get("certifications", [])
        st.caption(
            "Certifications: "
            + (", ".join(str(item) for item in certifications) or "None listed")
        )
        bonding = profile.get("bonding_capacity_cad")
        bonding_text = f"${bonding:,.0f}" if isinstance(bonding, (int, float)) else "Unavailable"
        st.caption(f"Bonding capacity: {bonding_text}")
        regions = profile.get("regions", [])
        st.caption(
            "Regions: " + (", ".join(str(item) for item in regions) or "None listed")
        )
    st.sidebar.caption("Sources: CanadaBuys open data + uploaded packages")

    counts = {
        verdict: sum(_valid_verdict(tender) == verdict for tender in tenders)
        for verdict in ("bid", "review", "no_bid", "not_analyzed")
    }
    st.sidebar.markdown("**Tender count**")
    st.sidebar.caption(
        f'Bid {counts["bid"]} · Review {counts["review"]} · '
        f'Don\'t bid {counts["no_bid"]} · Not yet analyzed {counts["not_analyzed"]}'
    )

    if "view" not in st.session_state:
        st.session_state.view = "Triage feed"
    st.sidebar.radio(
        "Navigate",
        ["Triage feed", "Compliance checklist", "Roadmap"],
        key="view",
    )


def main() -> None:
    """Render the read-only TenderSentry demo application."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    st.set_page_config(page_title=config.PRODUCT_NAME, layout="wide")
    data = load_all_data(_data_snapshot())
    tenders = data["tenders"]
    _render_sidebar(data["profile"], tenders)

    view = st.session_state.view
    if view == "Roadmap":
        _render_roadmap()
        return
    if view == "Compliance checklist":
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
