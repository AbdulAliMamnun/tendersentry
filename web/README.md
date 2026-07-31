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

## Environment

| Variable | Needed for | Notes |
|---|---|---|
| `BLOB_READ_WRITE_TOKEN` | Tender uploads | Injected automatically once a Blob store is linked |
| `RESEND_API_KEY` | Intake notifications | From resend.com |
| `NOTIFY_EMAIL` | Intake notifications | Where submissions are sent |
| `NOTIFY_FROM` | Optional | Defaults to Resend's onboarding sender |

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
