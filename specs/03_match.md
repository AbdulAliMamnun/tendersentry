# Spec 03 — Profile & Hybrid Qualification Engine (`match/engine.py`)

Read `SPEC.md` first. This module turns verified requirements + a company profile into a
Bid / Don't bid / Review verdict with named, cited blockers.

Trust rule, mirroring extraction: deterministic rules are preferred over LLM judgment; the LLM
judges ONLY requirements that rules cannot, and every judgment must reference a verified
requirement id. The engine can never cite anything that isn't in requirements.json.

---

## Part A — Demo profile (`data/profile.json`)

Create this file with exactly this demo firm (hardcoded is fine per SPEC.md scope):

```json
{
  "firm_name": "Georgian Bay Civil Ltd.",
  "certifications": ["COR"],
  "bonding_capacity_cad": 2000000,
  "insurance": {"cgl_limit": 5000000, "auto_limit": 2000000},
  "regions": ["Ontario"],
  "past_projects": [
    {"name": "Culvert replacement, Simcoe County", "value_cad": 850000, "year": 2024, "type": "civil"},
    {"name": "Pumphouse upgrade, Orillia", "value_cad": 1200000, "year": 2023, "type": "civil"},
    {"name": "Shoreline stabilization, Tay Twp", "value_cad": 400000, "year": 2022, "type": "civil"}
  ],
  "staff_designations": ["P.Eng on staff"],
  "submission_capabilities": ["email", "portal", "physical"]
}
```

Note what it lacks (ISO 9001, >$2M bonding) — deliberate, so the demo set produces Don't-bid
verdicts with real blockers.

## Part B — Deterministic rules (`evaluate_rule(req, profile) -> RuleResult`)

Operates ONLY on requirements where `machine_checkable == true`. Dispatch on `check_field`:

| check_field         | Logic |
|---------------------|-------|
| certification       | check_value (case-insensitive) in profile.certifications |
| bonding_capacity    | profile.bonding_capacity_cad >= numeric(check_value) |
| insurance_cgl (etc) | profile.insurance[limit] >= numeric(check_value) |
| region              | overlap between requirement region and profile.regions |
| submission_method   | required method in profile.submission_capabilities; `!=` passes when a non-prohibited alternative is supported |

- `numeric()` must parse "$2,000,000", "2M", "10% of bid price" — for percent-of-bid values with
  no bid amount available, the rule returns UNKNOWN (not pass, not fail).
- Unknown check_field or unparseable check_value → UNKNOWN, logged. Never guess.
- RuleResult: `{requirement_id, outcome: pass|fail|unknown, detail: str}` where detail is
  human-readable ("requires ISO 9001; profile holds [COR]").

## Part C — LLM judgment (`judge_fuzzy(reqs, profile, client) -> list[Judgment]`)

For requirements with `machine_checkable == false` AND `is_mandatory == true`, plus every
machine-checkable requirement whose deterministic outcome is UNKNOWN. Include the rule detail as
context for rule-unknown requirements. Batch at most 10 requirements per call. After each response,
retry omitted ids once, then default remaining omissions to uncertain. Model from config,
temperature 0, JSON mode.

System prompt requirements:
- Role: bid qualification analyst. For each requirement (given with its id, text, and verbatim
  quote), judge whether the firm profile satisfies it: satisfied | not_satisfied | uncertain.
- Judge ONLY from the profile fields provided. If the profile lacks the information needed,
  answer uncertain — never assume unstated capabilities.
- Every judgment must include: requirement_id, verdict, and a one-sentence rationale that
  references the requirement's substance.
- Return `{"judgments": [...]}`.

Post-process: any judgment whose requirement_id is not in the current batch is discarded and
logged (the mirror of the hallucination guard). Invalid verdicts are coerced to uncertain. Persist
judgment provenance as explicit, coerced, defaulted, or rule. Log the tally sent, returned,
accepted, coerced, and defaulted.

## Part D — Verdict (`decide(tender_id) -> dict`)

Inputs: requirements.json (verified only — this is automatic since dropped items never land
there) + profile.json + rule results + judgments.

Verdict logic, in order:
1. Any mandatory requirement with rule outcome=fail OR judgment=not_satisfied → verdict
   "no_bid"; those requirement ids are blockers.
2. Else if any mandatory requirement has outcome=unknown OR judgment=uncertain → verdict
   "review"; those ids listed as open_questions.
3. Else → verdict "bid".

Decision record (per SPEC.md schema, extended):
```json
{
  "tender_id": "...",
  "verdict": "bid | no_bid | review",
  "blockers": ["req ids"],
  "open_questions": ["req ids"],
  "rationale": "one paragraph, plain language, naming the decisive requirements",
  "confidence": "high (all deterministic) | medium (fuzzy judgments involved) | low (many unknowns)",
  "judgments": [{"id": "req id", "verdict": "satisfied | not_satisfied | uncertain", "source": "explicit | coerced | defaulted | rule"}],
  "counts": {"mandatory": N, "passed": N, "failed": N, "uncertain": N}
}
```
Write to `data/tenders/{tender_id}/decision.json`.

## Part E — `__main__`
`python -m match.engine [tender_id]` — one tender or all with a requirements.json. Print:
tender_id | verdict | mandatory | passed | failed | uncertain | blockers (first blocker's
requirement_text truncated to 50 chars). Log API usage.

## Acceptance criteria
1. Running against cb-757-46105229 with the demo profile produces a decision.json whose every
   blocker/open_question id exists in requirements.json.
2. Verdict logic is provably order-correct: a tender with one rule-fail is no_bid even if other
   items are uncertain.
3. Zero API calls for a tender whose mandatory requirements are all machine_checkable and every
   deterministic rule resolves to pass or fail; rule-unknown requirements fall through to fuzzy judgment.
4. Re-run without --force reuses decision.json.
5. Never crashes on: empty requirements.json (verdict "review", rationale "no requirements
   extracted"), missing profile fields (affected rules → unknown).

## Not in scope
No UI (spec 04), no multi-profile, no evaluation-criteria scoring, no win-probability.
