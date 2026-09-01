"use client";

import { Archivo } from "next/font/google";
import { useEffect, useRef, useState } from "react";

import { H2, P } from "@/components/guides/Prose";

/**
 * Loaded through next/font, not a stylesheet link: the file is self-hosted at build
 * time, so there is no render-blocking request to a third party and no layout shift
 * from a late swap.
 *
 * Scoped to the dark scroll story alone. Archivo's display weights are what carry that
 * composition, but the page sits inside the shared product shell — nav, H1 and footer
 * in the site face — and running a second font through the whole body would make this
 * page look broken beside the other three rather than deliberate.
 */
const archivo = Archivo({
  subsets: ["latin"],
  weight: ["500", "800"],
  display: "swap",
});

/**
 * Bid Confidence — a product that does not exist yet.
 *
 * Three scroll states in which one shape morphs: a single bid price widens into a
 * range of possible costs, then a contingency marker appears with everything past it
 * shaded. Then a four-stage arc, an illustrative contingency dial, and a capture form
 * asking what the thing should tell you.
 *
 * **Every number here is invented and says so where it appears.** The dial is a shape
 * of a trade-off, not a forecast, and nothing on this page is drawn from our data or
 * from a real tender. The rule the rest of the site follows — estimates are labelled
 * wherever they are shown — matters more here than anywhere, because a page about
 * pricing risk is exactly where a number would be mistaken for a measurement.
 *
 * Accessibility: the morphing SVG is `aria-hidden`. A single accessible name on it
 * could only narrate an animation nobody using a screen reader can perceive, or
 * describe one arbitrary frame of three. The three captions are real text in the DOM
 * in order, and each carries a visually-hidden description of its own figure, so the
 * screen-reader path gets the same three-step argument the reduced-motion path does.
 */

/** Geometry, matching the reference composition. */
const CX = 600;
const FLOOR = 640;
const AMP = 430;

const clamp = (v: number, a: number, b: number) => (v < a ? a : v > b ? b : v);
const window01 = (p: number, a: number, b: number) => clamp((p - a) / (b - a), 0, 1);
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** Gaussian, and the filled path under it between two x bounds. */
const gauss = (x: number, s: number) => Math.exp(-Math.pow(x - CX, 2) / (2 * s * s));
function shape(s: number, a: number, b: number): string {
  let d = `M ${a} ${FLOOR}`;
  for (let x = a; x <= b; x += 4) d += ` L ${x} ${(FLOOR - AMP * gauss(x, s)).toFixed(1)}`;
  return `${d} L ${b} ${FLOOR} Z`;
}

/** Sigma and marker position for a scroll position. One shape the whole way. */
function geometry(p: number) {
  const sigma = 7 + ease(window01(p, 0.18, 0.62)) * 168;
  return { sigma, marker: window01(p, 0.68, 0.86), quantile: CX + 0.842 * sigma };
}

type State = {
  heading: React.ReactNode;
  body: React.ReactNode;
  /** Read aloud in place of the figure. Written per state, never as one narration. */
  figure: string;
  /** Scroll position at which this state's figure is drawn, for the static branch. */
  at: number;
};

const STATES: State[] = [
  {
    heading: "You bid one number.",
    body: "Takeoff, subs, quotes, and a contingency you picked because it felt about right.",
    figure:
      "A single narrow vertical bar on a baseline, marked one million dollars: one bid price, with no width to it.",
    at: 0.15,
  },
  {
    heading: "It was always a range.",
    body: "Every line could land high or low, and some move together — the wet spring that hurts earthworks hurts drainage too.",
    figure:
      "The same bar has widened into a broad bell-shaped curve centred on that number: the range of costs the job could actually land at.",
    at: 0.62,
  },
  {
    heading: (
      <>
        Your contingency covers <span className="text-[#F0BE00]">this much</span> of it.
      </>
    ),
    body: (
      <>
        <span className="text-[#E4573D]">Everything past the marker</span> is a job that
        costs more than you bid. We show you where that marker actually sits, on a tender
        you&rsquo;re already looking at.
      </>
    ),
    figure:
      "A vertical marker stands to the right of the curve's centre, and the whole tail beyond it is shaded red: the share of outcomes that cost more than the bid.",
    at: 1,
  },
];

/** The dark composition, drawn at one scroll position. */
function Scene({ p, hidden }: { p: number; hidden: boolean }) {
  const { sigma, marker, quantile } = geometry(p);
  return (
    <svg
      viewBox="0 0 1200 800"
      preserveAspectRatio="xMidYMax meet"
      className="h-full w-full"
      aria-hidden={hidden ? "true" : undefined}
      role={hidden ? undefined : "img"}
    >
      <rect x={60} y={FLOOR} width={1080} height={2} fill="#333A3E" />
      <path
        d={shape(sigma, 60, 1140)}
        fill="#F0BE00"
        fillOpacity={0.16 + 0.06 * window01(p, 0.18, 0.62)}
        stroke="#F0BE00"
        strokeWidth={3}
      />
      <path d={shape(sigma, quantile, 1140)} fill="#C1352A" fillOpacity={0.55} opacity={marker} />
      <rect x={quantile} y={FLOOR - AMP} width={3} height={AMP} fill="#EDEDE6" opacity={marker} />
      <text
        x={clamp(quantile + 14, 0, 940)}
        y={FLOOR - AMP + 22}
        fontSize={17}
        fontWeight={700}
        fill="#E4573D"
        opacity={marker}
      >
        costs more than you bid
      </text>
      <text
        x={CX}
        y={FLOOR + 42}
        fontSize={22}
        fontWeight={900}
        fill="#EDEDE6"
        textAnchor="middle"
        opacity={window01(p, 0.04, 0.12)}
      >
        $1,000,000
      </text>
    </svg>
  );
}

function Caption({ state, className }: { state: State; className?: string }) {
  return (
    <div className={className}>
      <h2 className="max-w-[13ch] text-[clamp(1.6rem,5vw,2.6rem)] font-extrabold leading-[0.98] tracking-[-0.03em] text-[#EDEDE6]">
        {state.heading}
      </h2>
      <p className="mt-3 max-w-[40ch] text-[15px] font-medium leading-relaxed text-[#9AA1A4]">
        {state.body}
      </p>
      {/* The figure, in words, per state — the screen-reader path gets the same three
          steps rather than one summary of an animation it cannot perceive. */}
      <span className="sr-only">{state.figure}</span>
    </div>
  );
}

function ScrollStory() {
  const storyRef = useRef<HTMLElement | null>(null);
  const [p, setP] = useState(0);
  const [reduced, setReduced] = useState(true);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    if (reduced) return;
    let queued = false;
    const measure = () => {
      queued = false;
      const node = storyRef.current;
      if (!node) return;
      const rect = node.getBoundingClientRect();
      const run = rect.height - window.innerHeight;
      setP(clamp(-rect.top / (run || 1), 0, 1));
    };
    const onScroll = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [reduced]);

  // Reduced motion: three figures, each beside the caption written for it. Not one
  // frame with three captions stacked on it — at the end state "You bid one number"
  // sits next to a picture of a range, which is the opposite of what it says. The
  // argument is three steps, so it stays three steps.
  if (reduced) {
    return (
      <div className={`${archivo.className} mt-10 space-y-px overflow-hidden rounded-lg bg-[#14171A]`}>
        {STATES.map((state, index) => (
          <div key={index} className="px-5 py-7 sm:px-7">
            <Caption state={state} />
            <div className="mt-5 h-[240px] w-full sm:h-[280px]">
              <Scene p={state.at} hidden />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const opacity = [
    1 - window01(p, 0.2, 0.3),
    Math.min(window01(p, 0.26, 0.36), 1 - window01(p, 0.58, 0.68)),
    window01(p, 0.66, 0.76),
  ];

  return (
    <section ref={storyRef} className="relative mt-10 h-[300vh]">
      <div className={`${archivo.className} sticky top-0 h-screen overflow-hidden rounded-lg bg-[#14171A]`}>
        <div className="absolute inset-0">
          <Scene p={p} hidden />
        </div>
        {STATES.map((state, index) => (
          <Caption
            key={index}
            state={state}
            className="pointer-events-none absolute left-5 right-5 top-7 transition-opacity duration-150 sm:left-7 sm:right-7"
            // Opacity alone never hides text from a screen reader, which is what we
            // want: all three captions are announced, in order, regardless of scroll.
          />
        ))}
        <div
          aria-hidden="true"
          className="absolute bottom-5 left-5 text-xs font-bold text-[#6C7376] sm:left-7"
        >
          {p < 0.3 ? 1 : p < 0.66 ? 2 : 3} / 3
        </div>
      </div>
    </section>
  );
}

const ARC = [
  {
    title: "Find the work",
    body: "Every open tender in Ontario and Québec, ranked against the work your firm actually bids — not keyword alerts you delete unread.",
    tag: "Live today",
    tone: "live" as const,
  },
  {
    title: "Price it",
    body: "Yours. Your takeoff, your subs, your read of the site. We don't estimate jobs and won't pretend we can.",
    tag: "You already do this",
    tone: "yours" as const,
  },
  {
    title: "Know what you're risking",
    body: "The range behind your number, and what your contingency is actually buying you on this job.",
    tag: "Building this now",
    tone: "now" as const,
  },
  {
    title: "Don't get thrown out",
    body: "Mandatory requirements pulled from the tender document, quoted word for word with the page number. If we can't point at the line, we don't show it.",
    tag: "Live today",
    tone: "live" as const,
  },
];

const TAG_CLASS = {
  live: "border-hairline text-muted",
  yours: "border-dashed border-hairline text-muted",
  now: "border-[#F0BE00] bg-[#F0BE00] text-heading",
};

/** Illustrative model. Not a forecast, and not drawn from any real data. */
const JOBS = 20;
const SPREAD = 12;
const LEAN = 5;

/** Normal CDF, Abramowitz–Stegun 26.2.17. */
function normalCdf(z: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp((-z * z) / 2);
  const q =
    d *
    t *
    (1.330274 * Math.pow(t, 5) -
      1.821256 * Math.pow(t, 4) +
      1.781478 * Math.pow(t, 3) -
      0.356538 * t * t +
      0.3193815 * t);
  return z > 0 ? q : 1 - q;
}

const winsAt = (c: number) => JOBS * 0.45 * Math.exp(-0.09 * (c - LEAN));

function Dial() {
  const [pct, setPct] = useState(8);
  const won = winsAt(pct);
  const over = Math.round(won * normalCdf(pct / SPREAD));
  const foregone = Math.round(winsAt(LEAN) - won);
  const verdict =
    pct <= 6
      ? "Lean enough to win work you lose money on."
      : pct >= 13
        ? "Safe enough that the jobs go to someone else."
        : "No setting here is right for all twenty jobs.";

  return (
    <div className="mt-6 rounded-lg bg-page p-5 sm:p-7">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-brand-red">
        Illustrative model — invented numbers
      </p>
      <div className="mt-4 flex flex-wrap items-baseline gap-4">
        <div className="text-[clamp(2.6rem,7vw,4rem)] font-extrabold leading-none tracking-[-0.04em] text-heading">
          {pct}%
        </div>
        <p className="max-w-[24ch] text-sm font-medium text-muted">
          added to every estimate, on twenty $1M jobs you bid in a year
        </p>
      </div>

      <label htmlFor="contingency" className="sr-only">
        Contingency percentage
      </label>
      <input
        id="contingency"
        type="range"
        min={5}
        max={15}
        step={1}
        value={pct}
        onChange={(event) => setPct(Number(event.target.value))}
        aria-describedby="dial-assumptions"
        className="bc-range mt-5 w-full"
      />
      <div className="mt-2 flex justify-between text-xs font-semibold text-muted">
        <span>5% — lean</span>
        <span>15% — cautious</span>
      </div>

      <div className="mt-6 grid gap-px overflow-hidden rounded-md border border-hairline bg-hairline sm:grid-cols-3">
        {[
          { value: Math.round(won), label: "jobs you win, of twenty bid", bad: false },
          { value: over, label: "of those, cost more than you bid", bad: true },
          { value: foregone, label: "jobs a leaner number would have won", bad: false },
        ].map((cell) => (
          <div key={cell.label} className="bg-white px-4 pb-5 pt-4">
            <b
              className={`block text-[clamp(2rem,5vw,2.8rem)] font-extrabold leading-none tracking-[-0.04em] ${
                cell.bad ? "text-brand-red" : "text-heading"
              }`}
            >
              {cell.value}
            </b>
            <span className="mt-2 block max-w-[22ch] text-[13px] font-semibold text-muted">
              {cell.label}
            </span>
            {/* On the cells, not only in the paragraph below: a screenshot of this
                block travels without the paragraph. */}
            <span className="mt-1 block text-[11px] font-medium uppercase tracking-[0.1em] text-muted">
              Illustrative
            </span>
          </div>
        ))}
      </div>

      <p className="mt-5 max-w-[34ch] text-[17px] font-bold leading-tight tracking-[-0.02em] text-heading">
        {verdict}
      </p>
      <p
        id="dial-assumptions"
        className="mt-4 max-w-[70ch] border-t border-hairline pt-4 text-[13px] leading-relaxed text-muted"
      >
        <strong className="font-bold text-heading">
          These numbers are illustrative, not a forecast.
        </strong>{" "}
        They assume twenty $1M jobs, estimates landing within about 12% of true cost
        either way, and an assumed decline in competitiveness as price rises. Nothing
        here comes from your firm or a real tender — it shows the shape of the
        trade-off, not a price.
      </p>
    </div>
  );
}

function Capture() {
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setState("sending");
    setError("");
    try {
      const response = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "bid-confidence", email, notes: note }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { error?: string };
        setError(body.error ?? "That didn't send. Try again in a moment.");
        setState("error");
        return;
      }
      setState("sent");
    } catch {
      setError("That didn't send. Try again in a moment.");
      setState("error");
    }
  }

  if (state === "sent") {
    return (
      <p className="mt-6 rounded-lg border border-hairline bg-page p-5 text-[15px] text-body">
        Thanks — noted. We&rsquo;ll come back to you with what we build, and you&rsquo;ll
        see it before it ships.
      </p>
    );
  }

  return (
    <form onSubmit={submit} className="mt-6 grid max-w-[36rem] gap-3">
      <label htmlFor="bc-email" className="text-sm font-bold text-heading">
        Email
      </label>
      <input
        id="bc-email"
        type="email"
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        placeholder="you@yourfirm.ca"
        className="w-full rounded-lg border border-hairline bg-white px-4 py-3 text-[15px] text-heading outline-none placeholder:text-muted focus:border-brand-red"
      />
      <label htmlFor="bc-note" className="text-sm font-bold text-heading">
        Where do your estimates go wrong?
      </label>
      <textarea
        id="bc-note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={4}
        placeholder="Earthworks, every time. Rock we didn't know about."
        className="w-full rounded-lg border border-hairline bg-white px-4 py-3 text-[15px] text-heading outline-none placeholder:text-muted focus:border-brand-red"
      />
      {error ? <p className="text-sm text-brand-red">{error}</p> : null}
      <button
        type="submit"
        disabled={state === "sending"}
        className="justify-self-start rounded-lg bg-heading px-6 py-3 text-[15px] font-bold text-white hover:opacity-90 disabled:opacity-60"
      >
        {state === "sending" ? "Sending…" : "Send it"}
      </button>
    </form>
  );
}

export function BidConfidence() {
  return (
    <>
      <style>{`
        .bc-range{-webkit-appearance:none;appearance:none;background:transparent;cursor:grab}
        .bc-range::-webkit-slider-runnable-track{height:6px;background:var(--bc-track,#141719)}
        .bc-range::-moz-range-track{height:6px;background:#141719}
        .bc-range::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:28px;height:28px;margin-top:-11px;background:#1F5C3D;border:2.5px solid #141719;border-radius:2px}
        .bc-range::-moz-range-thumb{width:28px;height:28px;background:#1F5C3D;border:2.5px solid #141719;border-radius:2px}
      `}</style>

      <P>
        <strong>Bid Confidence is in development.</strong> Tender discovery and
        compliance checking are live; this is not. The page explains what we are
        building and asks what you would need from it — every figure below is invented
        to show a shape, and none of it comes from your firm or a real tender.
      </P>
      <P>
        You already price with a gut-feel number. Here is what that number is hiding.
      </P>

      <ScrollStory />

      <H2>Four things happen between finding a job and winning one worth having</H2>
      <P>We do three of them. The one we don&rsquo;t is the one you&rsquo;re best at.</P>

      <div className="mt-6 border-t-2 border-heading">
        {ARC.map((step) => (
          <div
            key={step.title}
            className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 gap-y-2 border-b border-hairline py-5"
          >
            <div>
              <h3 className="text-[17px] font-extrabold tracking-[-0.02em] text-heading sm:text-[19px]">
                {step.title}
              </h3>
              <p className="mt-1 max-w-[52ch] text-[15px] text-muted">{step.body}</p>
            </div>
            <span
              className={`whitespace-nowrap border px-2 py-1 text-[11px] font-bold ${TAG_CLASS[step.tone]}`}
            >
              {step.tag}
            </span>
          </div>
        ))}
      </div>

      <H2>One contingency, twenty jobs</H2>
      <P>
        Move the dial and watch the trade-off move with it. This is a made-up model on
        made-up jobs — it exists to show that no single contingency is right for all of
        them, not to tell you what yours should be.
      </P>
      <Dial />

      <H2>What would you want it to tell you?</H2>
      <P>
        We&rsquo;re building this now, and the defaults matter more than the maths. If
        you price civil work in Ontario or Québec, tell us how far off your numbers
        usually run and on which trades. We&rsquo;ll use it, and you&rsquo;ll get it
        first.
      </P>
      <Capture />
    </>
  );
}
