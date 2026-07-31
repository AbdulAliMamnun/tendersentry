# Recommendation evaluation

Snapshots are the measurement surface for ranking quality. Each one records what the
engine returned for a firm at a moment in time, plus the versions that produced it, so
a later change in precision can be attributed to a weights change, a mapping change,
or a code change rather than guessed at.

## Take a snapshot

```bash
python3 -m eval.snapshot --firm 1 --top 20
```

Writes `eval/snapshots/firm<N>-<timestamp>.json`. Every row carries `"label": null`.

## Label it

Set each row's `label` to one of:

| Label | Meaning |
|---|---|
| `relevant` | The firm would genuinely consider bidding this. |
| `marginal` | Real work of roughly the right kind, but the wrong size, region, or timing. |
| `irrelevant` | Not work this firm does, or not construction at all. |

Editing JSON by hand is fine for a handful of rows. For a real labelling session, use
the spreadsheet round-trip:

```bash
python3 -m eval.snapshot --to-csv eval/snapshots/firm1-<stamp>.json
# fill the label column in Excel, Numbers, or Sheets, then:
python3 -m eval.snapshot --from-csv eval/snapshots/firm1-<stamp>.csv
```

The CSV lands beside the snapshot with the same name, carries `rank, score, title,
buyer_name, closing_date, flags, notice_url, label`, and is written as UTF-8 with a
BOM so accented titles survive a double-click into Excel. Blank labels mean "not yet
labelled"; anything outside the three values is refused with the line number.

Import matches rows on `rank` **and** `notice_url`, so a CSV that was re-sorted, had
rows deleted, or belongs to a different snapshot is refused rather than applied to the
wrong tenders. Re-importing an unchanged file writes nothing. Adding an optional
`label_note` column is honoured — those notes are what tell you which component is
misfiring, and they persist in the JSON.

`labelled` flips to `true` automatically once every row carries a label.

Label the whole top-N, including the rows you disagree with, and label against the
firm's real appetite rather than the engine's stated reasoning. Judging the reasoning
instead of the recommendation is how an eval set quietly starts confirming the
scorer's own biases.

## Score it

```bash
python3 -m eval.snapshot --score eval/snapshots/firm1-<stamp>.json --k 20
```

`precision@k` counts `relevant` over all labelled rows in the first *k*. An unlabelled
snapshot reports "nothing labelled yet" rather than a misleading zero, and a cohort
with no labels reports `—` rather than 0.00.

The report splits on the `trade_unmapped` flag:

```
              rows  labelled  precision   breakdown
  overall       20        20       0.55   relevant 11, marginal 4, irrelevant 5
  mapped        13        13       0.77   relevant 10, marginal 2, irrelevant 1
  unmapped       7         7       0.14   relevant 1, marginal 2, irrelevant 4
```

That split is the open question: unmapped notices are kept deliberately and credited
0.3 when construction-coded, 0.15 otherwise. If the unmapped cohort labels out as
mostly irrelevant, the agreed fix is a ceiling on unmapped scores rather than
re-tuning the region or buyer weights, which would move every other cohort too.

## Comparing versions

Snapshot before and after a change, label both, and compare at the same *k*. Keep the
old snapshots — they are the only record of what a given weights or mapping version
actually produced. `git_revision` in each file ties the numbers back to the code.

Two things to watch when reading a comparison:

- **The candidate pool moves too.** `candidate_count` and `excluded_count` are in every
  snapshot for this reason: precision can improve simply because Stage 1 excluded more,
  which is a recall change wearing a precision costume.
- **The corpus is live.** Notices close and new ones arrive, so two snapshots taken days
  apart are not scoring the same population. For a clean A/B, take both snapshots from
  the same database state.
