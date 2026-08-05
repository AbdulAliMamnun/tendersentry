# Learned bid-recommendation model

Ranks tenders by how likely a firm is to **bid** on them, learned from SEAO's
published bidder lists. Trains entirely locally: no hosted model API is called at any
point, so a run costs nothing and works offline.

## Setup

```bash
brew install libomp                       # required: LightGBM will not import without it
pip install scikit-learn lightgbm sentence-transformers
```

`libomp` is the one non-obvious dependency. LightGBM installs cleanly from PyPI and
then fails at *import* with `Library not loaded: @rpath/libomp.dylib` — the wheel
links against the OpenMP runtime, which macOS does not ship. Installing the Python
package alone is not enough.

`sentence-transformers` pulls PyTorch (~2.5 GB) and downloads the embedding model
(~470 MB) on first use.

## Pipeline

```bash
python3 -m scripts.download_seao_history   # ~277 weekly files, ~3.4 GB, resumable
python3 -m model.dataset                   # entities + interactions from the cache
python3 -m model.train                     # features, splits, models, report
```

## Modules

| Module | Role |
|---|---|
| `dataset.py` | Parses OCDS releases into `firm_entities` and `bid_interactions` |
| `features.py` | Three ablatable feature groups, computed strictly as-of a date |
| `embeddings.py` | Local multilingual sentence embeddings, disk-cached |
| `train.py` | Temporal splits, baselines, ranking metrics, importance report |

## Embedding model

`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` — 384 dimensions,
multilingual. SEAO is French and CanadaBuys is English, and both must share one vector
space; an English-only model would treat half the corpus as noise. Verified
cross-lingually: French *égouts pluviaux* scores 0.51 against English *watermain
replacement* and 0.22 against French *mobilier de bureau*.

## Two things that shape everything downstream

**Single-bidder procurements are excluded from training.** 62% of SEAO procurements
name only the winner, and that firm won 98% of the time — treating those as bidding
behaviour would teach the model who *wins*, not who *bids*. Training uses the
competitive subset (2+ observed bidders) only. This leaves a known blind spot: a firm
may legitimately want sole-source-shaped work, and this data cannot teach us that.

**History never sees the future.** Every firm-history feature takes an `as_of` date
and filters interactions with a strict `<`. An interaction dated *on* the cutoff is
excluded, because a tender closing that day must not see bids placed that day.
`tests/test_model_features.py` asserts this rather than trusting it.

## Serving the live demo

```bash
python3 -m scripts.export_model_service    # writes web/data/model/
python3 -m unittest tests.test_service_parity tests.test_demo_rank
```

The exporter fits a model on everything up to `SERVING_CUTOFF` and writes five files
into `web/data/model/`: the booster as plain tree structures, the open-tender pool with
each notice's embedding as base64 float32, one centroid per trade slug, a copy of
`matchrec/trade_mapping.json`, and a manifest. Together they are ~9 MB and are
committed, because the site imports them statically — there is no runtime model load
and no API call in the request path.

**The demo does not embed the visitor's text.** MiniLM does not fit in a serverless
function, and embedding a description with a *different* model would make the cosine
against MiniLM pool vectors meaningless rather than merely worse. The description is
mapped to trade slugs by the deterministic rules, and the firm vector is the centroid
of the pool tenders carrying those slugs. Both sides of `cross_embedding_similarity`
therefore derive from the same slug assignment, so the demo leans harder on the mapping
rules than production does. **Demo behaviour is not model behaviour** — the manifest
says so too, so the caveat travels with the artifacts.

The demo firm is a cold start: every `firm_*` and `cross_*` history feature is zero
except `firm_days_since_last`. A declared region filters the pool and a declared job
size applies the same bounded ±10 modifier `matchrec.scoring` uses; neither is fed to
the model, because a declared median bid alongside zero interactions is a combination
the model never saw in training, and a gradient-boosted tree given an
out-of-distribution row returns a confident number rather than an error.

### Two tests hold the serving path up

`tests/test_service_parity.py` scores the same fixture vectors through LightGBM and
through the shipped TypeScript walker and requires agreement to 1e-6. A subtly wrong
traversal — the wrong inequality, the wrong missing-value default, features read in the
wrong order — still returns a plausible number, so parity is asserted rather than
assumed.

`tests/test_demo_rank.py` runs the *shipped* TypeScript under Node (via `sucrase`, see
`tests/ts_harness.py`) rather than a Python re-implementation, which would only prove
it agrees with itself. It covers derivation, the rate limiter on an injected clock, and
the end-to-end claim that a watermain description ranks water work above office
furniture.

### Eligibility is decided by trade agreement, not by cosine

A notice reaches the board only if its trade slugs overlap the firm's **and** the
overlap is not incidental — a construction tag that is a strict minority alongside
upkeep tags (`facility_maintenance`, `landscaping`, `snow_ice_management`) does not
count. `Grounds Maintenance`, tagged `[facility_maintenance, roadwork, landscaping,
building_general]`, is a groundskeeping contract, not roadwork.

A cosine floor of **0.35** then acts as a backstop. It is anchored on the cross-lingual
calibration above: 0.22 for unrelated text, 0.51 across a language boundary for related
text. It is deliberately not the primary gate — see the confound below.

Displayed fit is an **absolute** logistic on the raw model score, not a min-max over
the day's pool. Pool-relative scoring meant the best row always read 100, so a region
with nothing relevant in it produced confident garbage. Being monotone in the raw
score, the logistic preserves the model's ordering exactly while letting a weak match
read as weak: an Ontario watermain job scores ~19, a Québec one ~92.

### Known limit: the embedding signal is language-skewed

**Slug centroids are French-dominated**, because 1,297 of the 2,003 pool notices come
from SEAO. Cosine against them therefore partly measures how French a title is. The
effect is larger than the relevance signal it is supposed to carry:

| notice (English, Ontario query) | cosine | genuinely relevant |
|---|---|---|
| Grounds Maintenance | 0.614 | no |
| Janitorial Cleaning Services | 0.583 | no |
| Terrestrial Archaeology Services | 0.572 | no |
| South Campus Watermain Replacement | 0.493 | **yes** |
| Emergency Storm Drainage Reconstruction | 0.519 | **yes** |
| *typical French watermain notice* | 0.80–0.87 | yes |

Within English, the ordering is close to noise. **No absolute cosine threshold can
separate good from bad there** — a floor high enough to cut janitorial also cuts the
real watermain job. This is why trade agreement is the primary gate and the floor is
only a backstop.

The fix is to compute centroids per (slug, language) at export time so cosines are
comparable across the language boundary. That is a follow-up, not a patch: it changes
the relevance signal and needs its own before/after on both languages.

### Known limit: pool coverage is uneven by province

Most Ontario municipal notices sit behind portals TenderSentry monitors rather than
republishes. An Ontario-filtered ranking can therefore be genuinely thin — at the time
of writing, 2 open watermain notices and 2 roadwork. The widget says so on the card
instead of padding the list to ten rows.

## Contract-scale estimation (`model/scale.py`)

```bash
python3 -m model.inflation --extract data/statcan/18100289.csv   # once, or on refresh
python3 -m model.scale --report                                  # held-out comparison
python3 -m model.scale --backfill                                # write bands to the DB
```

Under 1% of open notices publish a value — 241 of 48,834 — so the first question a
contractor asks is unanswerable from the notice almost every time. But 199,714 past
procurements carry a winning bid amount, which *is* the contract value. Three tiers,
best-available wins: `published` (the buyer said so; nothing overrides it),
`estimated_model` (learned from comparable past contracts), `estimated_pattern`
(deterministic EN+FR wording markers). A notice with no signal gets `unknown` — a band
is never forced, because "we don't know" is useful to a contractor and a fabricated
band is not.

**Estimates are a filter and a bounded modifier, never a model feature.** A *published*
value two or more bands from the firm's declared size is a hard filter — the buyer
stated the size, so there is nothing to be wrong about. An estimate only adjusts. The
estimate is derived from the notice title and so is the trade match; feeding one into a
ranking that already uses the other would make "size fit" a restatement of "trade fit"
dressed as independent evidence, which is the same failure mode as a leaked feature.

### Held-out results

Temporal split at 2025-07-01: 168,541 train, 19,329 test. Band accuracy:

| | exact | within one band |
|---|---:|---:|
| always guess the most common band (`$100–500K`) | 34.4% | 89.8% |
| median lookup (trade × buyer type × region) | 34.9% | 89.2% |
| pattern rules alone | 4.1% | 9.1% |
| **GBM (title embedding + trade + buyer + region)** | **41.7%** | **92.1%** |

**The lookup baseline is barely better than a constant** — 34.9% against 34.4% for
always guessing `$100–500K`, and it is actually *worse* on within-one-band. That is a
real result, not a tuning failure: contract size within a trade varies enormously, and
the categorical keys carry almost no information on their own. The GBM's +7.3 points
over the constant comes from the title, which is where the size signal actually lives.

The GBM ships. But 41.7% exact means the specific band is wrong more often than right,
so **92.1% within one band is the honest headline**, and the UI presents every estimate
as approximate with its provenance attached. The lookup remains as the fallback when
the GBM is unavailable, and its weakness is the reason `estimated_pattern` exists at
all rather than leaning on the lookup for everything.

### Known limit: the corpus is Québec

199,644 of 199,714 priced awards are QC. **Ontario has nine.** So a band on an Ontario
notice is an inference from Québec comparables. This is the census finding again in a
different dimension: rich estimation where the data is open, near-nothing where it is
not.

**Watch item — the Ontario-side label source.** `data.ontario.ca`'s *Historical contract
data for highway construction and maintenance contracts* is the dataset that would
supply it. As of this writing it is a **listing, not data**: the CKAN API reports
`num_resources: 0`, licence `notspecified`, `current_as_of: 2017-04-03`, and the page
states the data is under review to determine whether it can be released publicly. All
eight Ontario contract/tender datasets on the portal return zero resources. If MTO's
contract data — or the associated quantity-sheets dataset — is ever released under an
open licence, that is the Ontario-side corpus to ingest. No weaker substitute is worth
taking.

### Known limit: the deflator is the wrong sector

Amounts are restated in current dollars with **StatCan table 18-10-0289-01**, geography
*Quebec*, type *Non-residential buildings [622]*, division *Division composite*, base
period **2023 = 100**. The adjustment is large and matters: $1.00 in 2018-Q1 is $1.62
today.

That is a **building** index deflating work that is overwhelmingly roadwork, watermain,
and paving — *engineering* construction. The correct instrument would be an
infrastructure or engineering construction price index, and **Statistics Canada does not
publish an active one**: table 18-10-0022 (Infrastructure construction price index) ends
2019 and 18-10-0096 (Highway construction price indexes) ends 1993, both inactive. The
choice is a live index for the wrong sector or a right-sector index that stopped seven
years ago. We use the former and label it wherever an adjusted figure surfaces. The gap
is in Canadian price statistics rather than in this pipeline, which is worth recording
because someone will eventually ask.

The Quebec non-residential series also only starts **2017-Q1**, so awards before then
are excluded rather than carried unadjusted — 3.8% of priced awards.

## Milestone 11 candidate: combine the two profile paths

Name lookup and description ranking overlap **2 of 8 rows** for the same firm
(GROUPE COLAS QUÉBEC INC., 2,900 bids since 2004, measured 2026-08-04). Neither is
wrong, and that is the finding:

- **History knows demonstrated capability at demonstrated scale.** What this firm has
  actually pursued, for which buyers, at what sizes. It returns tighter, more expensive
  roadwork — mostly `$500K–2M`.
- **The description knows current intent.** What the firm says it does *today*. It
  returns a wider spread skewing `$100–500K`.

For a firm that has shifted focus, the record is stale and the description is right.
For a firm describing itself loosely, the record is right.

The candidate is not to pick a winner but to carry both — description as intent,
history as capability — and **surface the disagreement rather than resolving it**. A
row the record ranks highly and the description does not is worth showing *as* a
disagreement: it is either work the firm has moved on from, or work it forgot it was
good at, and only the firm knows which.

Not built. Recorded so the observation is not lost.
