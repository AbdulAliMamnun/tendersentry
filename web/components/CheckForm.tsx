"use client";

import { upload } from "@vercel/blob/client";
import { useRef, useState } from "react";

const MAX_BYTES = 25 * 1024 * 1024;

/**
 * Free-check intake.
 *
 * The PDF goes straight from the browser to Blob storage rather than through an API
 * route: serverless request bodies cap at 4.5 MB and real tender packages are far
 * bigger, so the obvious path would reject exactly the documents this exists for.
 */
export function CheckForm() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [email, setEmail] = useState("");
  const [firm, setFirm] = useState("");
  const [noticeUrl, setNoticeUrl] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  // Captured at submit time: the form unmounts on success, taking the file input
  // with it, and a submitter should be able to check they sent the right document.
  const [submitted, setSubmitted] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0] ?? null;

    if (!file && !noticeUrl.trim()) {
      setState("error");
      setMessage("Add a tender PDF or a link to the notice.");
      return;
    }
    if (file && file.size > MAX_BYTES) {
      setState("error");
      setMessage("That file is over 25 MB — send the notice link instead.");
      return;
    }

    setState("sending");
    setMessage("");
    try {
      let documentUrl: string | null = null;
      if (file) {
        const blob = await upload(file.name, file, {
          access: "public",
          handleUploadUrl: "/api/check/upload",
        });
        documentUrl = blob.url;
      }

      const response = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "check",
          email,
          firm: firm.trim() || null,
          noticeUrl: noticeUrl.trim() || null,
          documentUrl,
          documentName: file?.name ?? null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Something went wrong");

      setSubmitted(file?.name ?? (noticeUrl.trim() || null));
      setState("done");
      setMessage("");
    } catch (error) {
      setState("error");
      setMessage(
        error instanceof Error ? error.message : "Something went wrong. Try again.",
      );
    }
  }

  if (state === "done") {
    return (
      <div className="card p-7">
        <p className="text-[15px] leading-relaxed text-body">
          <strong className="font-semibold text-heading">
            Got it — we&rsquo;ve received your tender.
          </strong>{" "}
          Your compliance brief will be sent to <strong>{email}</strong> within 24
          hours. If anything about the package is unclear, we&rsquo;ll reply to ask
          rather than guess.
        </p>
        {submitted ? (
          <p className="mt-5 truncate rounded-control border border-hairline bg-page px-4 py-3 text-sm text-body">
            <span className="text-muted">Received:</span> {submitted}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="card p-7">
      <div>
        <label htmlFor="email" className="block text-sm font-medium text-heading">
          Your email <span className="text-brand-red">*</span>
        </label>
        <input
          id="email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@firm.ca"
          className="field mt-2"
        />
      </div>

      <div className="mt-6">
        <label htmlFor="file" className="block text-sm font-medium text-heading">
          Tender PDF
        </label>
        <input
          id="file"
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          className="mt-2 block w-full text-sm text-body file:mr-4 file:rounded-control
            file:border file:border-hairline file:bg-page file:px-4 file:py-2.5
            file:text-sm file:font-medium file:text-body hover:file:border-muted"
        />
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Uploaded documents are stored privately-by-link and used only to prepare your
          brief.
        </p>
      </div>

      <div className="mt-6">
        <div className="flex items-center gap-3">
          <span className="h-px flex-1 bg-hairline" />
          <span className="text-xs uppercase tracking-wide text-muted">or</span>
          <span className="h-px flex-1 bg-hairline" />
        </div>
      </div>

      <div className="mt-6">
        <label htmlFor="notice" className="block text-sm font-medium text-heading">
          Link to the notice
        </label>
        <input
          id="notice"
          type="url"
          value={noticeUrl}
          onChange={(event) => setNoticeUrl(event.target.value)}
          placeholder="https://canadabuys.canada.ca/…"
          className="field mt-2"
        />
      </div>

      <div className="mt-6">
        <label htmlFor="firm" className="block text-sm font-medium text-heading">
          Firm name <span className="font-normal text-muted">(optional)</span>
        </label>
        <input
          id="firm"
          type="text"
          value={firm}
          onChange={(event) => setFirm(event.target.value)}
          placeholder="So we can flag what disqualifies you specifically"
          className="field mt-2"
        />
      </div>

      <button type="submit" className="btn-red mt-7 w-full" disabled={state === "sending"}>
        {state === "sending" ? "Sending…" : "Send it over"}
      </button>

      {state === "error" ? (
        <p className="mt-3 text-sm text-brand-red">{message}</p>
      ) : null}
    </form>
  );
}
