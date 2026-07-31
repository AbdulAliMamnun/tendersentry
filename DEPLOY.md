# Deploying the TenderSentry website to Vercel

The site is a static-first Next.js app in `web/`. Its data is committed JSON, so the
build needs neither Python nor the database.

## 1. Refresh the exported data

Run these whenever the pipeline has new results worth publishing:

```bash
cd /path/to/tendersentry
python3 -m scripts.export_demo_board --firm 1
python3 -m scripts.export_census
git add web/data && git commit -m "web: refresh exported data"
```

Both scripts are read-only against the database. `--firm 2` publishes the Québec
board instead of the Ontario one.

## 2. Create the Vercel project

1. Import the repository at [vercel.com/new](https://vercel.com/new).
2. **Set Root Directory to `web`.** This is the one setting that matters — the repo
   root is a Python project and will not build.
3. Framework preset: Next.js. Build command, output directory and install command are
   all detected; leave them.

## 3. Add a Blob store

Storage → Create → Blob, and connect it to the project. Vercel injects
`BLOB_READ_WRITE_TOKEN` automatically. Without it, tender uploads fail while the rest
of the site works.

## 4. Set environment variables

Project Settings → Environment Variables, for Production and Preview:

| Variable | Value |
|---|---|
| `RESEND_API_KEY` | An API key from [resend.com](https://resend.com) |
| `NOTIFY_EMAIL` | Where intake notifications go |
| `NOTIFY_FROM` | Optional sender, e.g. `TenderSentry <hello@yourdomain.ca>` |

`NOTIFY_FROM` requires a domain verified in Resend. Until then the default
`onboarding@resend.dev` sender works for notifications to yourself.

If the Resend variables are missing the intake route still returns success and logs
the submission — a visitor is never shown an error because a server secret is unset —
but nothing is emailed. Check the function logs before assuming no one has signed up.

## 5. Enable analytics

Project → Analytics → Enable Web Analytics. The `<Analytics />` component is already
in the root layout, so no code change is needed.

## 6. Deploy and verify

```bash
git push
```

Then check, on the deployed URL:

- **Homepage** — board card shows real rows and the fax blocker with its quote and
  page; the stat strip matches `web/data/stats.json`.
- **`/census`** — the lookup finds "Muskoka Lakes" and shows a green
  open-documents pill; the distribution table totals 444 municipalities.
- **`/check`** — submit with a small PDF and confirm the notification email arrives.
  Upload a file over 25 MB and confirm it is refused client-side.

## Custom domain

Project → Settings → Domains. Add the apex and `www`, and let Vercel issue the
certificate. No app changes are required; nothing in the code hardcodes a hostname.

## Rollback

Deployments → the last good build → Promote to Production. Because the data is
committed JSON rather than fetched at runtime, a rollback restores the exact numbers
that were live at that time.

## What deployment does not do

The website never runs the extraction pipeline. `/api/check` records a submission and
notifies a human; briefs are prepared deliberately. No visitor action can trigger
model spend, which is why the page promises 24 hours rather than seconds.
