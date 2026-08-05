import { A, H2, LI, P, UL } from "@/components/guides/Prose";
import { formatNumber, stats } from "@/lib/data";

export function Discovery() {
  return (
    <>
      <P>
        There is no single place Canadian public tenders are published. Federal work is
        on CanadaBuys, Québec work is on SEAO, and Ontario municipal work is scattered
        across two commercial platforms and 444 individual websites. Checking all of it
        is a job nobody has time for.
      </P>
      <P>
        We watch it and rank it. Today that is{" "}
        {formatNumber(stats.notices_tracked)} tracked notices, refreshed daily.
      </P>

      <H2>Two ways to tell us who you are</H2>

      <P>
        <strong>Describe your firm.</strong> Type what you build, where, and the job
        sizes you go after — &ldquo;watermain and sanitary sewer replacement, Montérégie,
        jobs around $500K&rdquo;. That is enough to rank the whole open market for you,
        and you can try it on the front page right now without an account.
      </P>
      <P>
        <strong>Or look up your bidding record.</strong> If your firm has bid on Québec
        public work, we already hold that history — it is published as open data — and
        we can rank from what you have actually pursued rather than from a description.
        That is the stronger of the two, and it is available to beta firms.
      </P>
      <P>
        The two know different things, and we have measured how differently: for the same
        firm they agree on 2 of 8 top results. The record knows what you have
        demonstrably done at what scale; the description knows what you are chasing now.
        Neither is wrong.
      </P>

      <H2>What the fit score means</H2>

      <P>
        A fit score is <strong>how much this tender looks like the work your firm goes
        after</strong> — not a chance of winning, and we are careful never to imply
        otherwise. It is an absolute number, not a position in the list: a 25 means a
        weak match, not fifth place. If today&rsquo;s market has nothing good for you,
        every score will be low, and that is the honest answer.
      </P>

      <H2>Why some tenders show an estimated size</H2>

      <P>
        Under 1% of notices publish a value. So where a buyer says nothing, we estimate
        the contract size from what comparable work has actually gone for — and we mark
        it as an estimate every time, because it is one.
      </P>
      <UL>
        <LI>
          <strong>&ldquo;$1.2M (published)&rdquo;</strong> — the buyer stated it.
        </LI>
        <LI>
          <strong>&ldquo;~$100–500K (estimated)&rdquo;</strong> — our figure, from
          similar past contracts.
        </LI>
        <LI>
          <strong>&ldquo;size unknown&rdquo;</strong> — we have no usable signal, which
          is true of about 44% of open notices. We would rather say so than guess.
        </LI>
      </UL>
      <P>
        Our estimates land in the exactly-right band 41.7% of the time and within one
        band 92.1% of the time, measured on contracts the model had never seen. Use them
        to sort a list, not to price a job. The{" "}
        <A href="/guides/what-a-200k-job-looks-like">full working is here</A>.
      </P>

      <H2>Coverage, stated plainly</H2>

      <P>
        <strong>Québec is well covered.</strong> SEAO publishes everything openly,
        including who bid on what, so both ranking and size estimation are strong there.
      </P>
      <P>
        <strong>Ontario is not, and no amount of effort on our side fixes it.</strong>{" "}
        We survey all {stats.municipalities_mapped} municipalities and ingest what is
        publicly posted, but most Ontario municipal tenders sit behind portals we monitor
        rather than republish. For some Ontario searches the honest result is a short
        list with a note saying it is short — which is what you will see, rather than a
        padded page. <A href="/research">The measurements are here</A>.
      </P>

      <P>
        If you work in Ontario, the practical use today is the compliance side and the
        federal and openly-posted municipal notices we do carry. We would rather tell you
        that up front than have you find out in month two.
      </P>
    </>
  );
}
