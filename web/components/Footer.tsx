import { GITHUB_URL } from "@/lib/data";
import { BetaForm } from "@/components/BetaForm";

/** Light footer: attribution on the left, the beta form on the right. */
export function Footer() {
  return (
    <footer id="join" className="border-t border-hairline bg-page">
      <div className="shell grid gap-10 py-14 md:grid-cols-2 md:gap-16">
        <div>
          <p className="text-[15px] font-semibold text-heading">Free while in beta</p>
          <p className="mt-2 max-w-sm text-sm leading-relaxed text-body">
            We are onboarding a small group of Ontario and Québec contractors. No card,
            no commitment.
          </p>
          <p className="mt-6 text-xs leading-relaxed text-muted">
            Built in Toronto ·{" "}
            <a
              href={`${GITHUB_URL}/blob/main/census/README.md`}
              className="underline underline-offset-2 hover:text-body"
            >
              Methodology on GitHub
            </a>{" "}
            · Municipal register:{" "}
            <a
              href="https://data.ontario.ca/dataset/ontario-municipalities"
              className="underline underline-offset-2 hover:text-body"
            >
              MMAH
            </a>{" "}
            (OGL-Ontario) · Population:{" "}
            <a
              href="https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810000201"
              className="underline underline-offset-2 hover:text-body"
            >
              StatCan
            </a>
          </p>
        </div>

        <div className="md:justify-self-end md:text-right">
          <BetaForm />
        </div>
      </div>
    </footer>
  );
}
