# Deploying the TenderSentry website to Vercel

The site is a static-first Next.js app in `web/`. Its data is committed JSON, so the
build needs neither Python nor the database.

Follow this top to bottom. Section 7 collects **every DNS record** — Vercel's and
Resend's — into one table so they can be pasted at the registrar in a single sitting.

---

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

Deploy once now, before adding storage or a domain, and confirm the preview URL
serves all three pages. Everything after this point is additive.

## 3. Add a Blob store

Storage → Create Database → **Blob** → connect it to this project.

Vercel injects `BLOB_READ_WRITE_TOKEN` automatically once connected; you never copy
it by hand. Without it the site works but tender uploads fail.

## 4. Create a Resend API key

1. Sign up at [resend.com](https://resend.com).
2. API Keys → Create API Key, with **Sending access**. Copy it once — it is shown a
   single time. It looks like `re_xxxxxxxx…`.
3. Domain verification comes later, in section 6. Until then Resend can still send
   from its shared `onboarding@resend.dev` sender, which is fine for notifications to
   yourself.

## 5. Set environment variables

Project Settings → Environment Variables. Add for **Production** and **Preview**:

| Variable | Value | Where it comes from |
|---|---|---|
| `BLOB_READ_WRITE_TOKEN` | *(automatic)* | Injected by the Blob store in section 3 — do not add by hand |
| `RESEND_API_KEY` | `re_…` | The key from section 4 |
| `NOTIFY_EMAIL` | your address | Where submission notifications are sent |
| `NOTIFY_FROM` | *(optional)* | Leave unset until section 6 completes, then `TenderSentry <hello@tendersentry.com>` |

Redeploy after adding variables — Vercel does not apply them to existing builds.

If the Resend variables are missing the intake route still returns success and logs
the submission; a visitor is never shown an error because a server secret is unset.
Check the function logs before assuming nobody has signed up.

## 6. Verify tendersentry.com in Resend

Do this *before* section 7 so the DKIM record it generates can be pasted alongside
the Vercel records.

1. Resend → Domains → **Add Domain** → `tendersentry.com`.
2. Choose a region. **Pick one and note it** — it determines the MX value below.
   `us-east-1` is the default and the right choice for Canadian traffic.
3. Resend then shows three records. Two are predictable; the DKIM public key is
   generated for your domain and must be copied from that screen.

Leave the tab open and continue to section 7.

## 7. All DNS records, in one place

At your registrar for `tendersentry.com`. **Keep DNS at the registrar** rather than
moving nameservers to Vercel — that way these records all live in one panel.

| # | Type | Name / Host | Value | TTL | Purpose |
|---|---|---|---|---|---|
| 1 | A | `@` | `76.76.21.21` | Auto | Apex → Vercel |
| 2 | CNAME | `www` | `cname.vercel-dns.com` | Auto | www → Vercel |
| 3 | MX | `send` | `feedback-smtp.us-east-1.amazonses.com` (priority **10**) | Auto | Resend bounce handling |
| 4 | TXT | `send` | `v=spf1 include:amazonses.com ~all` | Auto | Resend SPF |
| 5 | TXT | `resend._domainkey` | **Copy from Resend** — a long `p=MIGfMA0GCSqGSIb3DQEB…` string | Auto | Resend DKIM |
| 6 | TXT | `_dmarc` | `v=DMARC1; p=none;` | Auto | Optional but recommended |

Two of these are issued rather than fixed, so **trust the dashboards over this table**:

- **Record 1 and 2**: add the domain in Vercel first (Project → Settings → Domains →
  add `tendersentry.com` and `www.tendersentry.com`). Vercel displays the exact A and
  CNAME values for your project. `76.76.21.21` and `cname.vercel-dns.com` are the
  long-standing values, but Vercel has been migrating some projects to a different
  apex IP — **use whatever the Domains screen shows you.**
- **Record 5**: the DKIM key is unique to your domain and cannot be known in advance.
  Copy it verbatim from the Resend domain screen, including any trailing `=`.
- **Record 3**: the region in the hostname must match the region you chose in
  section 6. `eu-west-1` and `ap-northeast-1` are the alternatives.

Some registrars append the domain automatically. If yours shows `send.tendersentry.com`
after you type `send`, that is correct — do not enter the full domain twice.

**Propagation.** Vercel usually validates within minutes; Resend can take up to an
hour and re-checks on its own. Both dashboards show a green state when satisfied.

Once Resend reports Verified, set `NOTIFY_FROM` to
`TenderSentry <hello@tendersentry.com>` and redeploy.

## 8. Enable analytics

Project → Analytics → Enable Web Analytics. The `<Analytics />` component is already
in the root layout, so no code change is needed. It is cookieless and needs no consent
banner.

## 9. Verify the deployment

On the live domain:

- **Homepage** — the board shows real rows and the fax blocker with its quote and
  page; the stat strip matches `web/data/stats.json`.
- **`/census`** — the lookup finds "Muskoka Lakes" and shows a green open-documents
  pill; the distribution table totals 444 municipalities.
- **`/check`** — submit with a small PDF and confirm the notification email arrives at
  `NOTIFY_EMAIL`. Try a file over 25 MB and confirm it is refused in the browser.
- **`www` and apex** both resolve, and http redirects to https.

## Rollback

Deployments → the last good build → Promote to Production. Because the data is
committed JSON rather than fetched at runtime, a rollback restores the exact numbers
that were live at that time.

## What deployment does not do

The website never runs the extraction pipeline. `/api/check` records a submission and
notifies a human; briefs are prepared deliberately. No visitor action can trigger
model spend, which is why the page promises 24 hours rather than seconds.

## Secrets

No key belongs in the repository. `.env`, `.env.*` and `.vercel` are gitignored;
`.env.example` is tracked and is intentionally empty. `BLOB_READ_WRITE_TOKEN` is
injected by Vercel; `RESEND_API_KEY` is set only in the Vercel dashboard. If a key is
ever pasted into a file, rotate it in the provider rather than only removing the
commit.
