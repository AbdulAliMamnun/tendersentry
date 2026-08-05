import { A, Caveat, H2, H3, LI, P, Quote, Stat, UL } from "@/components/guides/Prose";
import { board, stats } from "@/lib/data";

/**
 * Original data point: our own verified extractions. Every clause quoted here was
 * checked character-for-character against the source PDF at the page cited — that
 * verification step is the reason we can quote at all.
 */
export function ClausesThatDisqualify() {
  return (
    <>
      <P>
        The bids that hurt most are not the ones you lose on price. They are the ones
        thrown out before anyone reads the price — technically compliant work, rejected
        on a clause nobody read closely enough.
      </P>
      <P>
        We extract requirements from tender documents and verify each quote against the
        source PDF before we will show it. Everything below is a real clause from a real
        Ontario document, quoted verbatim with its page number.
      </P>

      <Stat
        value={String(stats.requirements_verified)}
        label="Requirements extracted and verified character-for-character against the source PDF at the page cited."
        source="TenderSentry extraction pipeline, as of 2026-08-04."
      />

      <Caveat>
        In building that pipeline we caught {stats.fabrications_caught} extractions where
        a language model produced a quote that was not in the document. That is precisely
        why nothing reaches a board without page-level verification — and why we publish
        the count rather than quietly fixing it.
      </Caveat>

      <H2>1. Bid security: the instrument matters as much as the amount</H2>

      <P>
        Most contractors know to bring bid security. Fewer read which <em>form</em> is
        acceptable. A certified cheque where a bid bond is demanded — or the reverse —
        is a rejection, and the clause is usually one line in a general-conditions
        section.
      </P>
      <P>
        The variant that catches experienced bidders is the original-document rule:
      </P>

      <Quote cite="Township of Muskoka Lakes, tender document, clause TC-2.1">
        The original bid security must be received within three (3) working days of the
        tender closing.
      </Quote>

      <P>
        Read that carefully. The bid closes electronically; the <em>paper</em> follows.
        Three working days is not three days — a Thursday close with a Monday holiday
        means Wednesday. Firms that submit electronically and courier the original on the
        following Friday have submitted late, and the bid is dead on a technicality
        entirely within their control.
      </P>

      <H3>What to check, every time</H3>
      <UL>
        <LI>Which instruments are named as acceptable — and whether the list is exhaustive.</LI>
        <LI>Whether an original physical document is required after electronic close.</LI>
        <LI>The deadline for that original, counted in working days, against the calendar.</LI>
        <LI>Who the security must be made payable to — a legal name, not a short name.</LI>
      </UL>

      <H2>2. Submission method: the channel is a requirement</H2>

      <P>
        A submission method is a compliance requirement like any other, and it is where
        the most avoidable losses happen. Some documents still specify channels that a
        modern firm simply does not operate:
      </P>

      <Quote cite={`${board.blocker.title}, p.${board.blocker.page}`}>
        {board.blocker.quote}
      </Quote>

      <P>
        A fax number, in a federal RFSO. If your firm submits electronically only — as
        most do now — this tender is not a near-miss you should hurry on. It is one you
        should not start, and the only useful thing software can tell you about it is
        <em> don&rsquo;t bid</em>, with the sentence and the page so you can confirm the
        call yourself in thirty seconds.
      </P>
      <P>
        That is the entire design principle behind how we surface blockers: not a score,
        not a flag, but the clause and its location.
      </P>

      <H2>3. Physical delivery: an address is a trap when the clock is tight</H2>

      <P>
        Even where electronic submission is allowed, physical delivery requirements
        survive in addenda and in specific document sets. The recurring trap is a
        delivery address that differs from the buyer&rsquo;s main office, combined with a
        receipt-time rule rather than a postmark rule.
      </P>

      <Quote cite="Municipality of Kincardine, tender document, p.75">
        Bids must be received at the address noted above prior to the closing time.
      </Quote>

      <P>
        <em>Received</em>, not sent. A courier scan at 2:58 for a 3:00 close is not
        receipt if the package is still on the truck. Where a document says received,
        build the schedule around the courier&rsquo;s guaranteed delivery window, not its
        pickup deadline.
      </P>

      <H2>The pattern underneath all three</H2>

      <P>
        Each of these is a requirement about the <em>act</em> of bidding rather than
        about the work. That is why they get missed: an estimator reads the specification
        closely and skims the instructions to bidders, because the specification is where
        the job is. The instructions are where the disqualifications are.
      </P>
      <P>
        A practical habit that costs nothing: before pricing anything, read the
        instructions-to-bidders section end to end and write down every deadline that is
        not the closing date. In our experience the list is usually two or three items
        long, and one of them is usually a surprise.
      </P>

      <P>
        If you want a second pair of eyes on a specific document, you can{" "}
        <A href="/check">have us check one free</A> — we return the requirements we find
        with each one quoted and cited to its page, so you can verify every call we make.
      </P>
      <P>
        For how we decide which tenders are worth reading in the first place, see{" "}
        <A href="/guides/how-we-rank-tenders">how we rank tenders for a firm</A>.
      </P>
    </>
  );
}
