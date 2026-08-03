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

### Known limit: pool coverage is uneven by province

Most Ontario municipal notices sit behind portals TenderSentry monitors rather than
republishes. An Ontario-filtered ranking can therefore be genuinely thin — at the time
of writing, 2 open watermain notices and 2 roadwork. The widget says so on the card
instead of padding the list to ten rows.
