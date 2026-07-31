"""Streamlit demo UI for TenderSentry's on-disk pipeline results."""

from __future__ import annotations

import html
import json
import logging
import re
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
    "bid": "Bid",
    "review": "Review",
    "no_bid": "Don't bid",
    "not_analyzed": "Not analyzed",
}

VERDICT_ICONS = {"bid": "✓", "review": "⌕", "no_bid": "×", "not_analyzed": "·"}


def _read_json(path: Path, default: Any) -> Any:
    """Read JSON safely, returning the supplied default for missing/invalid files."""
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return default


def _notice_key(value: Any) -> str:
    """Match notice ids to their filesystem-safe tender directory names."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_-")


def _index_notices(notices: list[Any]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for item in notices:
        if not isinstance(item, dict) or not item.get("tender_id"):
            continue
        tender_id = str(item["tender_id"])
        indexed.setdefault(tender_id, item)
        indexed.setdefault(_notice_key(tender_id), item)
    return indexed


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
    notices_by_id = _index_notices(notices)
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
    if days < 0:
        text = f"Closed {text}"
    elif days < 14:
        text += f" · closes in {days} day{'s' if days != 1 else ''}"
    return text, days


def _render_styles() -> None:
    st.markdown(
        """
<style>
html, body, [class*="st-"], .stApp { color: #57534e; }
html, body, .stApp, [data-testid="stAppViewContainer"] { background: #faf9f7; }
#MainMenu, footer, [data-testid="stHeader"], [data-testid="stToolbar"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"] { display: none !important; }
.block-container { max-width: 1000px; padding-top: 1.75rem; padding-bottom: 3rem; }
[data-testid="stVerticalBlock"] { gap: 1rem; }
h1, h2, h3, h4, h5, h6, strong { color: #292524; }
h1 { font-size: 1.8rem; letter-spacing: -0.025em; }
h2 { font-size: 1.25rem; }
h3 { font-size: 1rem; }
p, li, label, [data-testid="stMarkdownContainer"] { color: #57534e; }
[data-testid="stCaptionContainer"], [data-testid="stMetricLabel"] { color: #a8a29e; font-size: 12px; }
[data-testid="stMetricValue"] { color: #292524; }
.stButton button, [data-testid="stPopover"] button {
  color: #57534e; border: 1px solid #e7e3da; background: #fff;
  border-radius: 10px; box-shadow: none; min-width: max-content;
  white-space: nowrap;
}
.stButton button p, [data-testid="stPopover"] button p { white-space: nowrap; }
div[class*="st-key-tender-"] {
  background: #fff; border: 1px solid #f0ede6 !important;
  border-radius: 16px; padding: 20px; box-shadow: none;
}
.estimator-card-header { display: flex; align-items: center; gap: 12px; }
.estimator-icon {
  align-items: center; display: inline-flex; justify-content: center;
  width: 34px; height: 34px; border-radius: 10px; flex: 0 0 34px;
  font-size: 20px; line-height: 1; font-weight: 600;
}
.estimator-icon-no_bid { color: #9f3838; background: #FCEBEB; }
.estimator-icon-review { color: #92651b; background: #fdf3dc; }
.estimator-icon-bid { color: #477054; background: #eaf5ed; }
.estimator-icon-not_analyzed { color: #78716c; background: #f5f5f4; }
.estimator-card-title { color: #292524; font-size: 16px; font-weight: 600; line-height: 1.3; }
.estimator-card-meta { color: #a8a29e; font-size: 12px; margin-top: 2px; }
.estimator-verdict { font-size: 13px; font-weight: 600; white-space: nowrap; }
.estimator-verdict-no_bid, .estimator-blocker { color: #9f3838; }
.estimator-verdict-review { color: #92651b; }
.estimator-verdict-bid { color: #477054; }
.estimator-plain-verdict { color: #9f3838; font-size: 13px; font-weight: 600; margin: 2px 0; }
.estimator-quote {
  background: #fafaf9; border-radius: 10px; color: #57534e;
  font-size: 13px; font-style: italic; line-height: 1.55; padding: 12px 14px;
}
.estimator-quote-page { color: #a8a29e; font-style: normal; white-space: nowrap; }
div[class*="st-key-requirement-"] {
  border: 0; border-top: 1px solid #f0ede6; border-radius: 0; padding: 0.6rem 0;
}
.estimator-status {
  display: inline-block; min-width: 5ch;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700;
}
.estimator-fail { color: #9f3838; font-weight: 700; }
.estimator-urgent { color: #92651b; font-weight: 600; }
.estimator-closed { color: #a8a29e; }
.estimator-letterhead {
  border-bottom: 1px solid #f0ede6; padding-bottom: 0.35rem; margin-bottom: 0.7rem;
}
.estimator-firm { text-align: right; white-space: nowrap; }
.estimator-product { font-size: 14px; }
@media print {
  [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"],
  [class*="st-key-no-print"], button { display: none !important; }
  html, body, [class*="st-"] { background: #fff !important; }
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


def _display_title(tender: dict) -> str:
    """Prefer a stored human title, with a readable slug fallback."""
    tender_id = str(tender.get("tender_id") or "Tender")
    candidates = [tender.get("title")]
    decision = tender.get("decision", {})
    if isinstance(decision, dict):
        candidates.extend(decision.get(key) for key in ("title", "tender_title", "project_title"))
    for requirement in tender.get("requirements", []):
        if isinstance(requirement, dict):
            candidates.extend(
                requirement.get(key) for key in ("title", "tender_title", "project_title")
            )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and value.casefold() != tender_id.casefold():
            return value

    words = re.split(r"[-_]+", tender_id)
    return " ".join(
        word.upper() if word.isalpha() and len(word) <= 3 else word.capitalize()
        for word in words
        if word
    )


def _render_quote(quote: Any, page_number: Any) -> None:
    """Render a prominent, safely escaped source quote and its page citation."""
    if not quote:
        return
    st.markdown(
        '<div class="estimator-quote">“'
        f'{html.escape(str(quote))}” '
        f'<span class="estimator-quote-page">· p.{html.escape(str(page_number or "?"))}</span>'
        "</div>",
        unsafe_allow_html=True,
    )


def _plain_english_blocker(reason: str, blocker: dict, profile: dict) -> str:
    """Translate the engine's compact blocker reason for an estimator."""
    required_match = re.search(r"requires one of \[([^\]]+)\]", reason, re.I)
    supported_match = re.search(r"profile supports \[([^\]]+)\]", reason, re.I)

    def values(match: re.Match[str] | None) -> list[str]:
        if not match:
            return []
        return [value.strip(" '\"") for value in match.group(1).split(",") if value.strip()]

    required = values(required_match)
    supported = values(supported_match)
    if not required and blocker.get("check_field") == "submission_method":
        required = [str(blocker.get("check_value") or "").strip()]
    if not supported:
        supported = [str(value) for value in profile.get("submission_capabilities", [])]

    labels = {"physical": "physical delivery", "fax": "fax submission", "email": "email", "portal": "portal"}
    if required:
        requirement_text = str(blocker.get("requirement_text") or "").lower()
        if "fax" in requirement_text or "facsimile" in requirement_text:
            required = ["fax"]
        requirement = " or ".join(labels.get(value.lower(), value) for value in required)
        capability = " and ".join(labels.get(value.lower(), value) for value in supported)
        if {value.lower() for value in supported}.issubset({"email", "portal"}):
            return f"Requires {requirement} — this firm submits electronically only."
        if capability:
            return f"Requires {requirement} — this firm submits by {capability} only."
        return f"Requires {requirement} — this firm does not support that submission method."
    return _truncate(str(blocker.get("requirement_text") or "A mandatory requirement is not met."), 150)


def _closing_board_text(value: Any) -> tuple[str, str]:
    closing = _parse_closing(value)
    if closing is None:
        return "Closing unavailable", ""
    days = (closing.date() - date.today()).days
    if days < 0:
        return f"Closed {closing.strftime('%a %b %-d, %Y')}", "estimator-closed"
    day_text = f"{days} day{'s' if days != 1 else ''}"
    css_class = "estimator-urgent" if days < 5 else ""
    return f"Closes {closing.strftime('%a %b %-d')} — {day_text}", css_class


def _counts_text(decision: dict) -> str:
    counts = decision.get("counts", {}) if isinstance(decision, dict) else {}
    return (
        f'Mandatory items: {counts.get("mandatory", 0)} · '
        f'Pass {counts.get("passed", 0)} · Fail {counts.get("failed", 0)} · '
        f'Unresolved {counts.get("uncertain", 0)}'
    )


def _bid_security_text(tender: dict) -> str | None:
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
        return None
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
            _render_quote(quote, requirement.get("page_number"))


def _select_tender(tender_id: str) -> None:
    st.session_state.selected_tender_id = tender_id
    st.session_state.view = "Bid checklist"


def _back_to_feed() -> None:
    st.session_state.view = "Bid board"


def _render_tender_card(tender: dict, profile: dict, analyzed: bool = True) -> None:
    tender_id = tender["tender_id"]
    decision = tender.get("decision", {})
    verdict = _valid_verdict(tender)
    requirements_by_id = _requirements_by_id(tender)
    display_title = _display_title(tender)

    safe_id = tender_id.replace(".", "-")
    with st.container(border=True, key=f"tender-{verdict}-{safe_id}"):
        closing_text, closing_class = _closing_board_text(tender.get("closing_date"))
        header_column, verdict_column = st.columns([8.3, 1.7], vertical_alignment="center")
        with header_column:
            st.markdown(
                '<div class="estimator-card-header">'
                f'<span class="estimator-icon estimator-icon-{verdict}">{VERDICT_ICONS[verdict]}</span>'
                '<div>'
                f'<div class="estimator-card-title">{html.escape(display_title)}</div>'
                f'<div class="estimator-card-meta {closing_class}">{html.escape(closing_text)}'
                f' · {html.escape(tender_id)}</div>'
                "</div></div>",
                unsafe_allow_html=True,
            )
        with verdict_column:
            st.markdown(
                f'<div class="estimator-verdict estimator-verdict-{verdict}">{VERDICT_LABELS[verdict]}</div>',
                unsafe_allow_html=True,
            )

        line_two_fragments = [
            fragment
            for fragment in (_bid_security_text(tender), _submission_text(tender))
            if fragment
        ]
        blocker: dict = {}
        if analyzed and verdict == "no_bid":
            blocker_ids = decision.get("blockers", [])
            blocker = requirements_by_id.get(str(blocker_ids[0]), {}) if blocker_ids else {}
            judgment = next(
                (item for item in decision.get("judgments", []) if str(item.get("id")) == str(blocker_ids[0])),
                {},
            )
            reason = str(judgment.get("reason") or judgment.get("match_reason") or blocker.get("reason") or "")
            st.markdown(
                f'<div class="estimator-plain-verdict">{html.escape(_plain_english_blocker(reason, blocker, profile))}</div>',
                unsafe_allow_html=True,
            )
        st.write(" · ".join(line_two_fragments))
        if blocker.get("verbatim_quote"):
            _render_quote(blocker["verbatim_quote"], blocker.get("page_number"))

        count_column, why_column, checklist_column = st.columns([7.1, 1.15, 1.35])
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


def _render_feed(tenders: list[dict], profile: dict) -> None:
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
    analyzed = sorted(
        [tender for tender in tenders if _valid_verdict(tender) != "not_analyzed"],
        key=_closing_sort_key,
    )
    st.subheader(f"Opportunities ({len(analyzed)})")
    for tender in analyzed:
        _render_tender_card(tender, profile)

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
            _render_tender_card(tender, profile, analyzed=False)


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
                _render_quote(quote, requirement.get("page_number"))
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
    display_title = _display_title(tender)
    st.title(display_title)
    cover_rows = []
    if display_title != tender["tender_id"]:
        cover_rows.append(("Solicitation number", tender["tender_id"]))
    cover_rows.extend(
        [
            ("Closing", closing_text),
            (
                "Submission method",
                _submission_text(tender).removeprefix("Submission: "),
            ),
        ]
    )
    cover = st.columns([1, 3])
    with cover[0]:
        for label, _ in cover_rows:
            st.markdown(f"**{label}**")
    with cover[1]:
        for _, value in cover_rows:
            st.write(value)


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
    _render_feed(tenders, data["profile"])


if __name__ == "__main__":
    main()
