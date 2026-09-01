import { GITHUB_URL } from "@/lib/data";

/**
 * Light footer: attribution and sources. No form, no anchor.
 *
 * It used to render a `BetaForm` as well, and because guide and product routes
 * render one too, nine pages shipped the same form twice — identical heading,
 * sub-line and button, within a screen of scrolling. Visible to every reader, and
 * in no single source file.
 *
 * Carries NO `id="join"`. It used to, and because every page renders a Footer while
 * guide and product routes declare their own `id="join"` too, nine pages shipped the
 * id twice — invalid HTML, and a browser resolves a fragment to the first match, so
 * the footer's copy was unreachable. Neither file was wrong on its own, which is why
 * nothing caught it.
 *
 * A page that wants a `#join` target or a form now declares one. Inheriting either
 * from a shared component is the property that caused both collisions, so the
 * property is gone rather than made conditional.
 */
export function Footer() {
  return (
    <footer className="border-t border-hairline bg-page">
      <div className="shell py-14">
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
      </div>
    </footer>
  );
}
