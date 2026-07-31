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
  kind?: "check" | "beta";
  email?: string;
  firm?: string | null;
  noticeUrl?: string | null;
  documentUrl?: string | null;
  documentName?: string | null;
};

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

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

  const kind = payload.kind === "beta" ? "beta" : "check";
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
    return NextResponse.json({ ok: true, delivered: false });
  }

  const lines = [
    `Type: ${kind === "beta" ? "Beta signup" : "Free tender check"}`,
    `Email: ${email}`,
    payload.firm ? `Firm: ${payload.firm}` : null,
    payload.noticeUrl ? `Notice URL: ${payload.noticeUrl}` : null,
    payload.documentUrl
      ? `Document: ${payload.documentName ?? "upload"} — ${payload.documentUrl}`
      : null,
    `Received: ${new Date().toISOString()}`,
  ].filter(Boolean);

  try {
    const resend = new Resend(apiKey);
    await resend.emails.send({
      from: process.env.NOTIFY_FROM ?? "TenderSentry <onboarding@resend.dev>",
      to: notify,
      replyTo: email,
      subject:
        kind === "beta"
          ? `Beta signup — ${email}`
          : `Tender check — ${payload.firm ?? email}`,
      text: lines.join("\n"),
    });
  } catch (error) {
    console.error("Notification failed", error);
    return NextResponse.json(
      { error: "We couldn't record that just now. Please try again." },
      { status: 502 },
    );
  }

  return NextResponse.json({ ok: true, delivered: true });
}
