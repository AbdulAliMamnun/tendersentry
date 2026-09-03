import { A, H2, LI, P, Quote, UL } from "@/components/guides/Prose";
import { board, stats } from "@/lib/data";

export function Compliance() {
  return (
    <>
      <P>
        The bids that hurt are not the ones you lose on price. They are the ones thrown
        out before anyone opens the price envelope — good work, rejected on a clause
        buried in the instructions to bidders.
      </P>
      <P>
        Send us a tender package. We read it and return every mandatory requirement,
        each one <strong>quoted word for word with the page it appears on</strong>, so
        you can check our work in thirty seconds rather than take it on faith.
      </P>

      <H2>Why the quote and the page number are the whole product</H2>

      <P>
        Any software can tell you a tender &ldquo;requires bid security&rdquo;. That is
        not useful — you still have to go find the clause and read what form it has to
        take and when the original is due. And you cannot tell whether the software read
        the document correctly or made it up.
      </P>
      <P>
        So we do not ship a summary. We ship the sentence and its location, and we verify
        that sentence against the source PDF before it reaches you. To date that is{" "}
        {stats.requirements_verified} verified requirements. In building the pipeline we
        caught {stats.fabrications_caught} cases where the extraction produced a quote
        that was not in the document — which is exactly why nothing goes out unverified,
        and why we publish that number instead of quietly fixing it.
      </P>

      <H2>What comes back</H2>

      <P>Three kinds of thing, in plain language:</P>
      <UL>
        <LI>
          <strong>Mandatory requirements</strong> — bid security, insurance limits,
          bonding, certifications, references, submission format. Each quoted and cited.
        </LI>
        <LI>
          <strong>Deadlines that are not the closing date.</strong> These are where firms
          lose bids they had already won on merit.
        </LI>
        <LI>
          <strong>Blockers</strong> — requirements your firm cannot meet, flagged against
          your profile, with the clause attached so you can confirm the call.
        </LI>
      </UL>

      <H2>Three real examples</H2>

      <P>
        <strong>The instrument, and the clock on the original.</strong> A tender closes
        electronically, and then the paper has to follow:
      </P>
      <Quote disqualifying cite="Township of Muskoka Lakes, tender document, clause TC-2.1">
        The original bid security must be received within three (3) working days of the
        tender closing.
      </Quote>
      <P>
        Three <em>working</em> days is not three days. A Thursday close with a Monday
        holiday means Wednesday — and a firm that couriers the original on Friday has
        submitted late on a technicality entirely within its own control.
      </P>

      <P>
        <strong>Received, not sent.</strong>
      </P>
      <Quote disqualifying cite="Municipality of Kincardine, tender document, p.75">
        Bids must be received at the address noted above prior to the closing time.
      </Quote>
      <P>
        A courier scan at 2:58 for a 3:00 close is not receipt if the package is still on
        the truck. Where a document says <em>received</em>, build the schedule around the
        guaranteed delivery window, not the pickup deadline.
      </P>

      <P>
        <strong>A channel your firm does not have.</strong>
      </P>
      <Quote disqualifying cite={`${board.blocker.title}, p.${board.blocker.page}`}>
        {board.blocker.quote}
      </Quote>
      <P>
        A fax number, in a federal solicitation. If you submit electronically only, this
        is not a tender to hurry on — it is one not to start. The only useful thing
        software can say about it is <em>don&rsquo;t bid</em>, with the sentence and the
        page so you can confirm it yourself.
      </P>

      <H2>How to try it</H2>

      <P>
        Send one tender package and we will return the brief, free, within 24 hours. No
        account, no card. If it is not useful you have lost nothing but the upload.
      </P>
      <P>
        The longer version of what these clauses do and how to catch them yourself is in{" "}
        <A href="/guides/clauses-that-disqualify-compliant-bids">
          the clauses that disqualify compliant bids
        </A>
        .
      </P>
    </>
  );
}
