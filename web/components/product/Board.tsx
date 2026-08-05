import { A, H2, LI, P, UL } from "@/components/guides/Prose";

export function Board() {
  return (
    <>
      <P>
        A board is the thing you actually use week to week: a private page listing what
        is worth bidding right now, in order, with the tenders you should not touch
        marked and the reason quoted.
      </P>
      <P>
        It is the same ranking you can try on the front page, except built for your firm
        specifically and paired with the compliance read — so a tender that fits your
        trades but demands a submission method you do not have shows up as{" "}
        <strong>don&rsquo;t bid</strong>, with the clause and its page number, rather
        than near the top of your list.
      </P>

      <H2>What you get</H2>

      <UL>
        <LI>
          <strong>A private link.</strong> One URL, yours, no login. The link is the
          access — so it is not indexed, not shared, and not guessable.
        </LI>
        <LI>
          <strong>Updated weekly.</strong> New notices ranked, closed ones dropped.
        </LI>
        <LI>
          <strong>Blockers surfaced, not buried.</strong> Where we have read the
          documents, disqualifying clauses appear on the board itself with the quote and
          page.
        </LI>
        <LI>
          <strong>Estimated job sizes,</strong> labelled as estimates, so you can skip
          the ones that are the wrong size for your crew without opening them.
        </LI>
      </UL>

      <H2>How onboarding works today</H2>

      <P>
        Honestly: manually, and deliberately so. You send us a short description of your
        firm — trades, regions, typical job size, bonding capacity, anything that
        disqualifies you — and we build the board and send you the link. There is no
        signup flow yet, no dashboard, and no settings page.
      </P>
      <P>
        That is not a placeholder for something automated we are hiding. During the beta
        a human reads what you send, which means the profile is better than a form would
        produce and we find out what the form should have asked. When there are enough
        firms that this stops working, we will build the self-serve version.
      </P>
      <P>
        If your firm has bid on Québec public procurement, we can also build the profile
        from your actual bidding record rather than from your description — more precise,
        because it is what you have demonstrably pursued rather than how you describe
        yourself. Ask when you sign up.
      </P>

      <H2>What it costs, and what it is worth being clear about</H2>

      <P>
        Free during the beta, for Ontario and Québec contractors. We are not asking for
        payment details.
      </P>
      <P>
        In return we would like to hear when a board is wrong — a tender ranked high that
        you would never bid, or a blocker we called that was not one. That feedback is
        worth more to us right now than money, and it is why the beta is small.
      </P>

      <H2>Before you sign up, two limits</H2>

      <P>
        <strong>Coverage is much better in Québec than Ontario.</strong> Québec publishes
        its procurement openly; most Ontario municipal tenders sit behind portals we
        monitor rather than republish. An Ontario firm gets a thinner board today, and we
        would rather say so now. <A href="/research">The measurements are here</A>.
      </P>
      <P>
        <strong>The ranking is bid fit, not a prediction that you will win.</strong> It
        tells you where your estimating time is best spent. Whether you win is still
        about your price and your people.
      </P>
      <P>
        The method behind all of it, including the parts that do not work well, is in{" "}
        <A href="/guides/how-we-rank-tenders">how we rank tenders for a firm</A>.
      </P>
    </>
  );
}
