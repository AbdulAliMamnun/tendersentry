# TenderSentry — public website

Next.js 15 (App Router) + Tailwind, deployed on Vercel. Three pages: the homepage,
the Ontario tender access census, and the free tender check.

```bash
cd web
npm install
npm run dev        # http://localhost:3000
npm run build
```

## Where the numbers come from

Nothing on this site is a hand-authored figure. Every number, board row, quote and
percentage is exported from the Python pipeline at build time:

```bash
python3 -m scripts.export_demo_board --firm 1   # web/data/demo-board.json, stats.json
python3 -m scripts.export_census                # web/data/census.json
```

`--firm 2` exports the Québec firm's board instead. The exports are committed, so a
Vercel build needs no Python and no database.

The demo board combines two engines deliberately: the ranked rows come from the
recommendation engine, and the red blocker comes from the citation-verified
qualification engine — its quote was checked character-for-character against the
source PDF at the page shown. If no verified blocker exists, the export refuses to
emit a board rather than shipping an unevidenced one.

## Beta boards

Each firm gets a private board at `/board/{token}`. There is no login — **the
unguessable token is the access**, the same privately-by-link model as `/check`
uploads.

### ⛔ Blocking rule: real firms' boards must not be committed to git

This repository is public. Board **files** are committed today, which is acceptable
only because firms 1 and 2 are fictional demo profiles.

**Before onboarding firm 3 if it is a real customer, board JSON must move to
Blob-backed lookup (hash-keyed paths) and out of git.** This is a precondition, not a
suggestion. Committing a real contractor's board publishes their firm name and their
ranked opportunity list to anyone browsing the repository.

Tokens themselves are already safe: board files are named `sha256(token)`, so the
repository holds a value nothing can be derived from. The token exists only in the
database and in the link you send.

**The same wall is arriving from a second direction, and it is measured.** The daily
refresh commits `data/tendersentry-slim.db`, whose `firm_notice_exclusions` table
holds one row per firm per notice — ~99% of tenders, so **~59k rows and ~14.2 MB per
firm**. Staged bytes per run are roughly `43.5 + 6.3 + 14.2 × firms` MB, against the
200 MB ceiling in `.github/workflows/daily-refresh.yml`:

| firms | 3 | 4 | 6 | 10 | 11 |
|---|---|---|---|---|---|
| staged | 92.4 MB | 106.6 | 135.0 | 191.8 | **206.0 — trips** |

The per-firm term scales with the pool too: double the notices and it is ~28 MB per
firm, which moves the ceiling to the sixth firm; triple them and it is the fourth.

**The privacy rule above binds first — firm 3 against firm 11 — and it binds
absolutely rather than on a threshold.** The size ceiling is the backstop for the case
where per-firm data is still in the repository when it should not be. They are one
limit counted two ways: per-firm data cannot keep living here. Raising the ceiling is
not the fix.

### The flow

1. **A contractor submits the beta form.** It arrives as an email with subject
   `Board request — {firm}`, carrying their trades, regions and typical job size in
   their own words.
2. **You create the firm profile** in the `firms` table, mapping their description
   onto the controlled vocabulary. A board token is minted automatically at creation.
3. **Run the ranking and export:**
   ```bash
   python3 -m matchrec.rank --firm N
   python3 -m scripts.export_firm_boards
   ```
4. **Deploy** — commit `web/data/boards/` and push; Vercel builds.
5. **Send the firm its link, once.** `python3 -m profiles.tokens --show` prints every
   firm's board path. Send it privately; it is the credential.
6. **Weekly:** re-run the ingest, ranking and export, then push. Every board refreshes
   together, which is what the "updated weekly" badge on the page promises.

`python3 -m profiles.tokens --backfill` mints tokens for any firm missing one and is
idempotent. `--rotate N` replaces one firm's token and **invalidates the link already
sent** — only use it if a link leaked.

### Why boards are not prerendered

Static generation would require enumerating every token at build time, which means
committing the tokens themselves. The route hashes the token from the URL and looks
up the file instead, so tokens never enter the repository. Boards also carry
`noindex` and `referrer: no-referrer` — the latter because a board's URL *is* its
credential, and it would otherwise ride the `Referer` header to every tender notice a
contractor clicks through to. Analytics is disabled on `/board` for the same reason:
it records pathnames.

## Environment

| Variable | Needed for | Notes |
|---|---|---|
| `BLOB_READ_WRITE_TOKEN` | Tender uploads | Injected automatically once a Blob store is linked |
| `RESEND_API_KEY` | Intake notifications | From resend.com |
| `NOTIFY_EMAIL` | Intake notifications | Where submissions are sent |
| `NOTIFY_FROM` | Optional | Defaults to `TenderSentry <notifications@tendersentry.com>`. **Must be an address at a domain verified in Resend** — a domain-scoped key rejects anything else, including Resend's shared `onboarding@resend.dev` |

Without `RESEND_API_KEY`/`NOTIFY_EMAIL` the intake route still accepts submissions and
logs them rather than failing the visitor.

## Design system

Warm editorial, light throughout — **there are no dark sections anywhere.** `#292524`
is type and primary buttons, never a background band.

| Token | Value | Use |
|---|---|---|
| Page | `#faf9f7` | Every background, including the footer |
| Card | `#ffffff` | Cards and inputs |
| Hairline | `#f0ede6` | 1px borders and dividers |
| Heading | `#292524` | Headings, primary buttons |
| Body | `#57534e` | Body copy |
| Muted | `#a8a29e` | Captions, metadata |
| Red | `#A32D2D` | Blockers and accent CTAs **only** |
| Fit green | `#477054` | Fit scores, open-document pills, live dot |

Radii 12–16px, system font stack, generous whitespace, mobile-responsive.

## Uploads

Tender PDFs go **directly from the browser to Vercel Blob**, signed by
`/api/check/upload`. This is not incidental: Vercel serverless request bodies cap at
4.5 MB, and real tender packages run 34–93 pages — a naive `POST` through an API route
would reject exactly the documents this feature exists for. The route constrains
uploads to a single PDF of at most 25 MB.

V1 is manual-assisted by design. `/api/check` notifies a human and stops; it never
invokes the extraction pipeline, so no visitor can trigger unattended model spend.

## Analytics

Vercel Web Analytics — cookieless, no personal data, no consent banner required.

## French — documented fast-follow, not built

The site is English-only. Roughly half the tender corpus is French (SEAO), the census
covers Ontario, and a Québec demo firm already exists in the pipeline as firm 2, so
French is a market requirement rather than a nicety. When it is built:

- Use Next.js i18n routing with `/fr` paths rather than a language switcher on one route.
- The pipeline is already bilingual — trade mapping carries French keyword lists, and
  SEAO titles map correctly — so no Python work is needed to support it.
- The census is Ontario-only; a French census page should either wait for a Québec
  equivalent or state plainly that Québec publishes centrally and needs no such survey.

Nothing in this repository has been built for French. This section is the plan, not a
partial implementation.
