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
