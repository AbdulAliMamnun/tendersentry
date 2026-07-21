# TenderSentry

**Citation-verified compliance intelligence for Canadian public tenders.**

Under Canada's Contract A framework, one missed mandatory clause voids an otherwise winning bid. Estimators read 34–93-page tender packages under deadline pressure, and the clauses that disqualify them are often buried deep — page 75 of a 93-page RFT. TenderSentry extracts every mandatory requirement, proves each one with a verbatim quote and true page number, and tells a contractor whether they can even submit.

## The core invariant

**Every displayed requirement carries a verbatim quote and a true page number, verified by exact-substring match against the source PDF.** If the model's quote can't be found on the cited page, the requirement is repaired or dropped — never shown. No hallucinated compliance advice, ever.

## What it does

1. **Ingest** — CanadaBuys tender discovery (CSV + scraper fallback) and raw PDF ingestion with a page-tagged provenance store (pdfplumber)
2. **Extract** — map-reduce extraction over page-tagged chunks (GPT-4o, temperature 0, JSON mode)
3. **Verify** — the hallucination guard: exact-substring check of every quote against its cited page, with cross-page repair; failures are dropped
4. **Classify** — each requirement labeled bid-phase mandatory vs. contract condition vs. not-a-requirement
5. **Qualify** — hybrid engine: deterministic checks against a firm profile (certifications, bonding, insurance, regions, submission methods) plus a batched fuzzy LLM judge with judgment provenance
6. **Present** — a bid board sorted by closing date; red is reserved exclusively for bid-voiding blockers; every blocker card shows the disqualifying clause and its page

## Real results (demo set: 4 real Ontario/federal tenders, 34–93 pages)

| Tender | Pages | Extracted | Verified | Dropped by guard | Verdict |
|---|---|---|---|---|---|
| Federal crane rental RFSO | 55 | 98 | 92 | 6 | No bid (fax submission required) |
| Muskoka Lakes T-2026-31 | 34 | 33 | 31 | 2 | No bid (physical delivery required) |
| Augusta PW-2026-04 | 65 | 88 | 87 | 1 | No bid (physical delivery to clerk's office) |
| Kincardine CS-2025-01 | 93 | 79 | 76 | 3 | Review (33 items for human judgment) |

**12 fabricated or unverifiable requirements were caught and dropped by the guard before any human saw them.**

Buried clauses it surfaced, with citations:
- **Kincardine, page 75 of 93**: electronic bids must still hand-deliver the original bid security to the municipal office within 3 working days of the deadline
- **Muskoka, TC-2.1 (page 6)**: tender deposit accepted only as bid bond or irrevocable letter of credit — certified cheques, accepted by neighbouring municipalities, are silently excluded

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env   # add your OPENAI_API_KEY
python3 -m extract.pipeline <tender_id> --force   # extraction + verification
python3 -m match.engine <tender_id> --force       # qualification
streamlit run ui/app.py
```

Tender PDFs live in `data/tenders/<tender_id>/raw/`. The firm profile is `data/profile.json`.

## Known limitations and roadmap

- The qualification engine's deterministic field set covers certifications, bonding capacity, insurance limits, regions, and submission methods. Instrument-type checks (e.g. failing a firm whose bid security is a certified cheque against a bonds-only tender) are extracted and displayed but not yet machine-checked — next increment.
- Deadline-date comparison against the current date is not yet implemented.
- Extraction precision was verified by hand on the flagship kill-shots; a full sampled precision/recall audit is the next validation step.
- One unsupported machine-check pattern (`bonding_capacity in <region>`) is downgraded to fuzzy judgment with a logged warning.

Built solo during OpenAI Build Week, July 2026.
