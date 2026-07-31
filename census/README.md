# Ontario municipal procurement census — methodology

A census of how each of Ontario's 444 municipalities publishes its tender notices:
on its own website, on a gated procurement platform, or not discoverably at all.

Retrieved **2026-07-31**. Reproduce with `python3 -m census.run`; re-print the
distribution without re-fetching with `python3 -m census.run --report-only`.

## Sources

### Municipal register

| | |
|---|---|
| Dataset | [Municipalities](https://data.ontario.ca/dataset/ontario-municipalities), data.ontario.ca |
| Dataset ID | `62e83cbc-0731-4d66-abdc-2f2b31bcd76c` |
| Resource | `municipalities_-_en_2026-0526.csv` |
| Publisher | Ministry of Municipal Affairs and Housing |
| Licence | Open Government Licence – Ontario |
| Retrieved | 2026-07-31 |
| Rows | 444 |

Discovered through the portal's CKAN API rather than by a hard-coded file URL, so a
re-run picks up the current resource. The register's `Municipality` column is an HTML
anchor whose `href` is the municipality's official website: **440 of 444 carry one,
all 440 hosts distinct.** This is why the census never guesses a domain.

`Municipal status` gives the tier directly — Lower 241, Single 173, Upper 30. Upper-tier
counties and regions run their own procurement and are included.

### Population

| | |
|---|---|
| Table | Statistics Canada 98-10-0002, Population and dwelling counts, 2021 Census |
| Retrieved via | WDS API `getFullTableDownloadCSV/98100002/en` |
| Geographies used | Ontario census divisions (`2021A000335…`) and subdivisions (`2021A000535…`) |
| Match rate | **442 of 444 (99.5%)** |

**Tier-aware DGUID disambiguation.** StatCan publishes bare names, so "Hamilton" is
three rows: the census division, the City of Hamilton (CSD `2021A00053525005`,
569,353), and Hamilton Township in Northumberland (CSD `2021A00053514019`, 11,059).
Only the DGUID separates them. The join therefore:

1. matches **upper-tier** municipalities against census **divisions**;
2. matches everything else against census **subdivisions**;
3. where a subdivision name is ambiguous, resolves it using the division code implied
   by the register's `Geographic area` (Hamilton Township → Northumberland → `3514`);
4. leaves the population **unset** when the collision cannot be resolved, rather than
   picking one;
5. falls back to the division figure only for **single-tier** municipalities, which
   are their own census divisions.

A flat name index — the first implementation — gave the City of Hamilton the
township's 11,059 residents and gave five upper-tier counties (Waterloo, Essex, Perth,
Peterborough, Renfrew) their like-named town's figure. That understated the
platform-held population share by roughly one point.

**The two unmatched municipalities**, both genuine name differences, left unset rather
than hand-mapped:

| Register | StatCan |
|---|---|
| Tarbutt, Township of | Tarbutt and Tarbutt Additional |
| The Nation Municipality | La Nation |

The MMAH CSV also ships **double-encoded UTF-8** ("Mattice-Val CÃ´tÃ©" for
"Mattice-Val Côté"); this is repaired on read.

## Discovery

Path guessing does not work. Of four Ontario municipalities checked by hand before
building anything, **none** used `/tenders`, `/bids`, `/procurement`, or `/rfp`:

| Municipality | Actual path |
|---|---|
| Muskoka Lakes | `/township-hall/bids-and-tenders/` |
| Kincardine | `/our-services/bids-and-tenders/` |
| Orillia | `/build-and-invest/business-opportunities-and-resources/procurement/` |
| Grey County | `/government/budget-finances-purchasing/bids-tenders-contracts` |

All four, however, link procurement from the homepage. So discovery harvests and
scores links instead:

1. Fetch the homepage and score every link on href + anchor text. `bids and tenders`
   and `bid opportunit` weigh most; `procurement` and `bidding` mid; `purchasing`,
   `doing business` and `supplier` least. Negative weights push down `budget`,
   `by-law`, `policy`, `minutes`, `agenda`, `career`, `job`, `news`. Grey County's
   homepage surfaces five `budget-finances-purchasing/…` links before the real one, so
   `purchasing` alone must not win.
2. Follow the best-scoring same-host candidate.
3. If that page scores below the "strong" threshold it is treated as a hub and one
   further hop is allowed — how a site that files tenders only under "Doing Business"
   is reached.
4. Only if the homepage yields nothing are the conventional paths tried.

**At most three requests per municipality** in the common case: `robots.txt`, homepage,
procurement page.

## Classification

Evaluated in this order; the first match wins, and the deciding URL is stored as
evidence alongside `checked_at`.

| # | Class | Meaning |
|---|---|---|
| 1 | `robots_disallowed` | robots.txt forbids the path. Nothing is fetched. |
| 2 | `no_website_listed` | The register lists no website. Never fetched. |
| 3 | `fetch_failed` | The homepage refused us (403), was absent (404/5xx), or the host would not resolve or present a valid certificate. |
| 4 | `bids_and_tenders` | The page links or names `bidsandtenders.ca`. |
| 5 | `biddingo` | The page links or names `biddingo.com`. |
| 6 | `bidnet_or_other_platform` | Another platform: bidnetdirect, MERX, Bonfire, Ariba, Jaggaer, QuestCDN, IonWave. The platform is recorded. |
| 7 | `own_site_open` | Tender-patterned documents are downloadable from the municipal site — filenames or link text matching `T-2026-31`, `RFP 2026-04`, or the words tender/quotation/invitation to bid. |
| 8 | `own_site_notices` | A procurement page exists, but its documents are policy-shaped (by-laws, procedures, safety policies) or absent. |
| 9 | `no_procurement_page_found` | The homepage was read successfully and no procurement page was found. |

A platform reference always beats a document count. Kincardine's page carries eight
PDFs — a procurement by-law, a contractor safety policy, a council report — while its
actual tenders sit on a gated platform whose link is JavaScript-rendered and therefore
invisible in the served HTML. Without rule 4 preceding rule 7, and without separating
tender-patterned documents from policy-shaped ones, it classifies as an open poster.
It is kept as a permanent test fixture for that reason.

### Confidence

`own_site_open` also carries `confidence`:

- **high** — at least two tender-patterned documents, outnumbering the policy-shaped
  ones, with no vendor-registration language (or overwhelming document evidence
  regardless: above five tender documents, registration language elsewhere on the page
  no longer casts doubt).
- **low** — thin or mixed evidence.

Phase B processes high-confidence municipalities first; low-confidence ones are
"verify by parsing" rather than fact, so a misclassification costs ordering rather
than correctness. The split is 26 high / 21 low.

## Politeness guarantees

Enforced in `census/fetcher.py` rather than left to callers:

- **User agent** `TenderSentryBot` on every request, including `robots.txt`.
- **≥5 seconds between requests to the same host**, enforced by a lock that reserves
  the slot before sleeping, so concurrent workers queue rather than race. Workers run
  across *different* hosts; no host is ever polled faster than the floor.
- **robots.txt honoured.** Fetched once per host and cached. A missing or unreachable
  robots.txt is treated as permission, per convention; an explicit `Disallow` is
  obeyed and the municipality is recorded as `robots_disallowed` without a fetch.
  Two municipalities fall in this class: Rideau Lakes and Sioux Narrows-Nestor Falls.
- **Platform-host blocklist.** Requests to bidsandtenders, biddingo, bidnetdirect,
  MERX, Bonfire, Ariba, Jaggaer, QuestCDN, IonWave and similar raise rather than
  proceed — including when a municipal page links to them, and including a redirect
  that lands on one mid-request. Their terms prohibit scraping; the census records
  only *that* a municipality uses them.
- **No login flows, ever.** Public pages only. Nothing authenticates, and no document
  is downloaded — Phase B stores links.

## Results

444 municipalities, retrieved 2026-07-31.

| Classification | Municipalities | % of munis | Population | % of pop |
|---|---:|---:|---:|---:|
| `bids_and_tenders` | 161 | 36.3 | 11,569,394 | 51.0 |
| `own_site_notices` | 79 | 17.8 | 1,889,339 | 8.3 |
| `no_procurement_page_found` | 74 | 16.7 | 5,613,053 | 24.7 |
| `own_site_open` | 47 | 10.6 | 332,059 | 1.5 |
| `fetch_failed` | 39 | 8.8 | 2,203,207 | 9.7 |
| `biddingo` | 32 | 7.2 | 771,650 | 3.4 |
| `bidnet_or_other_platform` | 6 | 1.4 | 217,701 | 1.0 |
| `no_website_listed` | 4 | 0.9 | 95,100 | 0.4 |
| `robots_disallowed` | 2 | 0.5 | 11,610 | 0.1 |

Total counted population 22,703,113.

**Gated platforms hold 199 municipalities and 12,558,745 people — 55.3% of the
counted population.** bids&tenders alone accounts for 161 municipalities and 51.0%.

**Open posters: 46 municipalities and 170,279 people, 0.75% of the counted
population.** This excludes the County of Frontenac (see below), whose 161,780
residents would otherwise account for nearly half the class's population on the
strength of a register error. The headline including it — 47 municipalities and
1.5% — overstates the class by a factor of two.

Open posting is real and almost entirely small townships. The population is with the
platforms.

## Caveats

**The register contains at least one wrong website.** MMAH lists
`frontenacislands.ca` — the Township of Frontenac Islands — as the County of
Frontenac's site. Discovery correctly stayed on the host it was given, so that row's
verdict describes the township while its population describes the county. It is
excluded from the `own_site_open` headline above and left uncorrected in the data
rather than silently repointed.

**`fetch_failed` is not "no procurement page".** 39 municipalities could not be read
at all. Ottawa and Vaughan return **403 to `TenderSentryBot`** — they block the bot
outright, which says nothing about whether they publish openly. Others had dead
hostnames or certificate hostname mismatches. Treating these as absent pages
understates inventory, and an earlier version of this census did exactly that.

**`no_procurement_page_found` is a floor, not a fact.** 74 municipalities, 24.7% of
population, including Toronto, Mississauga, Halton and Kitchener. Their homepages are
JavaScript-rendered or their procurement entry points sit behind navigation this
census cannot see without executing scripts. A headless browser would raise this
number; that was deliberately not built.

**JavaScript-rendered pages are invisible throughout.** This affects both discovery
(a homepage whose navigation is client-rendered yields no links) and classification
(Kincardine's platform link never appears in the served HTML). Where it matters, the
result is a conservative class — `no_procurement_page_found` or a low-confidence
`own_site_notices` — never a false claim of open documents.

**Population is 2021 Census.** Five years stale as of retrieval, and shares will drift
in fast-growing municipalities.

**A classification is a snapshot.** `checked_at` records when each was decided;
municipalities move onto and off platforms. `python3 -m census.run --recheck-class
<class>` re-checks one class without re-walking the whole register.
