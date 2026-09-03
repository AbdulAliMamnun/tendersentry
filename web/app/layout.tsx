import type { Metadata } from "next";
import { SiteAnalytics } from "@/components/SiteAnalytics";
import "./globals.css";

export const metadata: Metadata = {
  title: "TenderSentry — Bid the right tenders. Skip the wrong ones.",
  description:
    "TenderSentry watches the whole Canadian tender market, ranks what fits your firm, " +
    "and proves every disqualifying clause — the sentence, and the page it's on.",
  openGraph: {
    title: "TenderSentry",
    description:
      "Ranked tender opportunities for Ontario and Québec contractors, with every " +
      "disqualifying clause cited to its page.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white font-sans antialiased">
        {children}
        <SiteAnalytics />
      </body>
    </html>
  );
}
