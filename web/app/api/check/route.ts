import { NextResponse } from "next/server";
import { Resend } from "resend";

/**
 * Intake for free checks and beta signups.
 *
 * V1 is manual-assisted on purpose: this route notifies a human and stops. It does
 * not invoke the extraction pipeline, so no submission can trigger unattended model
 * spend. The notification email is the record of truth — there is no database to
 * migrate or leak while the volume is this small.
 */

type Payload = {
  kind?: "check" | "beta" | "board";
  email?: string;
  firm?: string | null;
  noticeUrl?: string | null;
  documentUrl?: string | null;
  documentName?: string | null;
  trades?: string | null;
  regions?: string | null;
  jobSize?: string | null;
};

const SUBJECTS = {
  check: "Tender check",
  board: "Board request",
  beta: "Beta signup",
} as const;

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Must be an address at a domain verified in Resend. The API key is scoped to
 * tendersentry.com, so Resend's shared `onboarding@resend.dev` sender — the previous
 * default — was rejected outright.
 */
const DEFAULT_FROM = "TenderSentry <notifications@tendersentry.com>";

export async function POST(request: Request) {
  let payload: Payload;
  try {
    payload = (await request.json()) as Payload;
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }

  const email = String(payload.email ?? "").trim();
  if (!EMAIL_PATTERN.test(email)) {
    return NextResponse.json({ error: "Enter a valid email address." }, { status: 400 });
  }

  const kind =
    payload.kind === "beta" || payload.kind === "board" ? payload.kind : "check";
  if (kind === "check" && !payload.documentUrl && !payload.noticeUrl) {
    return NextResponse.json(
      { error: "Add a tender PDF or a link to the notice." },
      { status: 400 },
    );
  }

  const apiKey = process.env.RESEND_API_KEY;
  const notify = process.env.NOTIFY_EMAIL;
  if (!apiKey || !notify) {
    // Never fail the visitor for a missing server secret; log it and accept.
    console.error("Intake received but RESEND_API_KEY or NOTIFY_EMAIL is unset", {
      kind,
      email,
    });
    return NextResponse.json({ ok: true });
  }

  const lines = [
    `Type: ${SUBJECTS[kind]}`,
    `Email: ${email}`,
    payload.firm ? `Firm: ${payload.firm}` : null,
    payload.trades ? `Trades: ${payload.trades}` : null,
    payload.regions ? `Regions: ${payload.regions}` : null,
    payload.jobSize ? `Typical job size: ${payload.jobSize}` : null,
    payload.noticeUrl ? `Notice URL: ${payload.noticeUrl}` : null,
    payload.documentUrl
      ? `Document: ${payload.documentName ?? "upload"} — ${payload.documentUrl}`
      : null,
    `Received: ${new Date().toISOString()}`,
  ].filter(Boolean);

  const from = process.env.NOTIFY_FROM ?? DEFAULT_FROM;

  try {
    const resend = new Resend(apiKey);
    const { data, error } = await resend.emails.send({
      from,
      to: notify,
      replyTo: email,
      // Distinct per kind so board requests are filterable in the inbox.
      subject: `${SUBJECTS[kind]} — ${payload.firm ?? email}`,
      text: lines.join("\n"),
    });

    if (error) {
      // The SDK reports API rejections in the result rather than throwing, so a
      // 403 from a domain-scoped key used to fall straight through to "delivered".
      // The sender is logged because it is the usual cause.
      console.error("Resend rejected the notification", {
        from,
        to: notify,
        kind,
        name: error.name,
        message: error.message,
        error,
      });
    } else {
      console.info("Notification sent", { id: data?.id, kind, from });
    }
  } catch (error) {
    console.error("Notification failed to send", { from, to: notify, kind, error });
  }

  // Always friendly: the submission itself succeeded — the document is in Blob
  // storage and recoverable from the logs — so a notification problem is ours to
  // chase, not something to show a contractor as a failure.
  return NextResponse.json({ ok: true });
}
