import unittest
from pathlib import Path

from census import classify, schema


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "census"
PAGE_URL = "https://www.example.ca/business/tenders"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RealPageTests(unittest.TestCase):
    """One saved page per class, captured from live Ontario municipal sites."""

    def test_muskoka_lakes_is_an_open_poster(self) -> None:
        verdict = classify.classify_page(
            _fixture("muskoka_lakes_own_site_open.html"), PAGE_URL
        )

        self.assertEqual(verdict["classification"], schema.CLASS_OWN_SITE_OPEN)
        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_HIGH)
        self.assertIsNone(verdict["platform"])
        self.assertGreater(len(verdict["documents"]), 20)

    def test_the_muskoka_documents_include_a_real_tender_package(self) -> None:
        verdict = classify.classify_page(
            _fixture("muskoka_lakes_own_site_open.html"), PAGE_URL
        )

        filenames = " ".join(document["filename"] for document in verdict["documents"])
        self.assertIn("t-2026-28", filenames)

    def test_orillia_is_on_bids_and_tenders(self) -> None:
        verdict = classify.classify_page(
            _fixture("orillia_bids_and_tenders.html"), PAGE_URL
        )

        self.assertEqual(verdict["classification"], schema.CLASS_BIDS_AND_TENDERS)
        self.assertEqual(verdict["platform"], "bidsandtenders")
        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_HIGH)

    def test_grey_county_is_on_bonfire(self) -> None:
        verdict = classify.classify_page(_fixture("grey_county_bonfire.html"), PAGE_URL)

        self.assertEqual(verdict["classification"], schema.CLASS_OTHER_PLATFORM)
        self.assertEqual(verdict["platform"], "bonfire")

    def test_kincardine_is_not_mistaken_for_an_open_poster(self) -> None:
        # The permanent fixture for the hazard: its page carries eight PDFs, all
        # policy documents, while its tenders live on a gated platform whose link
        # is JavaScript-rendered and therefore invisible here.
        verdict = classify.classify_page(
            _fixture("kincardine_low_confidence.html"), PAGE_URL
        )

        self.assertNotEqual(verdict["classification"], schema.CLASS_OWN_SITE_OPEN)
        self.assertEqual(verdict["classification"], schema.CLASS_OWN_SITE_NOTICES)
        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_LOW)

    def test_a_cms_fingerprint_is_recorded_for_phase_b(self) -> None:
        for name in (
            "muskoka_lakes_own_site_open.html",
            "kincardine_low_confidence.html",
            "orillia_bids_and_tenders.html",
        ):
            with self.subTest(page=name):
                verdict = classify.classify_page(_fixture(name), PAGE_URL)
                self.assertEqual(verdict["cms_fingerprint"], "escribe")


class DocumentTests(unittest.TestCase):
    def test_tender_patterned_filenames_are_recognized(self) -> None:
        html = """
        <a href="/media/x/t-2026-31-supply-granular.pdf">T-2026-31 Granular</a>
        <a href="/media/y/rfp-2026-04-engineering.pdf">RFP 2026-04</a>
        <a href="/media/z/notes.pdf">Committee notes</a>
        """
        documents = classify.collect_documents(html, PAGE_URL)

        by_name = {document["filename"]: document for document in documents}
        self.assertTrue(by_name["t-2026-31-supply-granular.pdf"]["is_tender"])
        self.assertTrue(by_name["rfp-2026-04-engineering.pdf"]["is_tender"])
        self.assertFalse(by_name["notes.pdf"]["is_tender"])

    def test_policy_documents_are_told_apart_from_tenders(self) -> None:
        html = """
        <a href="/media/a/procurement-policy-by-law.pdf">Procurement Policy</a>
        <a href="/media/b/contractor-safety-policy.pdf">Contractor Safety Policy</a>
        """
        verdict = classify.classify_page(html, PAGE_URL)

        self.assertEqual(verdict["classification"], schema.CLASS_OWN_SITE_NOTICES)
        self.assertEqual(verdict["documents"], [])

    def test_platform_hosted_documents_are_never_collected(self) -> None:
        html = (
            '<a href="https://town.bidsandtenders.ca/docs/t-2026-01.pdf">T-2026-01</a>'
            '<a href="/media/a/t-2026-02-roadwork.pdf">T-2026-02</a>'
        )

        documents = classify.collect_documents(html, PAGE_URL)

        self.assertEqual(len(documents), 1)
        self.assertIn("t-2026-02", documents[0]["url"])

    def test_relative_document_urls_are_resolved(self) -> None:
        documents = classify.collect_documents(
            '<a href="/media/a/t-2026-02.pdf">T</a>', PAGE_URL
        )

        self.assertEqual(documents[0]["url"], "https://www.example.ca/media/a/t-2026-02.pdf")


class ClassificationOrderTests(unittest.TestCase):
    def test_a_platform_link_beats_documents_on_the_same_page(self) -> None:
        html = (
            '<a href="/media/a/t-2026-31-tender.pdf">T-2026-31</a>'
            '<a href="/media/b/t-2026-32-tender.pdf">T-2026-32</a>'
            '<a href="https://town.bidsandtenders.ca/Module/Tenders/en">Bid portal</a>'
        )

        verdict = classify.classify_page(html, PAGE_URL)

        self.assertEqual(verdict["classification"], schema.CLASS_BIDS_AND_TENDERS)

    def test_notices_without_documents_are_own_site_notices(self) -> None:
        html = "<h1>Current tenders</h1><p>T-2026-31 closing date August 4.</p>"

        verdict = classify.classify_page(html, PAGE_URL)

        self.assertEqual(verdict["classification"], schema.CLASS_OWN_SITE_NOTICES)

    def test_a_page_about_nothing_procurement_is_none_found(self) -> None:
        verdict = classify.classify_page("<h1>Parks and recreation</h1>", PAGE_URL)

        self.assertEqual(verdict["classification"], schema.CLASS_NONE_FOUND)

    def test_one_tender_document_is_kept_but_marked_low_confidence(self) -> None:
        verdict = classify.classify_page(
            '<a href="/media/a/t-2026-31-tender.pdf">T-2026-31</a>', PAGE_URL
        )

        self.assertEqual(verdict["classification"], schema.CLASS_OWN_SITE_OPEN)
        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_LOW)

    def test_registration_language_lowers_confidence_when_evidence_is_thin(self) -> None:
        html = (
            '<a href="/media/a/t-2026-31-tender.pdf">T-2026-31</a>'
            '<a href="/media/b/t-2026-32-tender.pdf">T-2026-32</a>'
            "<p>Bidders must be registered to bid on this opportunity.</p>"
        )

        verdict = classify.classify_page(html, PAGE_URL)

        self.assertEqual(verdict["classification"], schema.CLASS_OWN_SITE_OPEN)
        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_LOW)

    def test_navigation_boilerplate_is_not_mistaken_for_gating(self) -> None:
        # "Register to Vote (Elections Ontario)" appears in municipal navigation and
        # briefly downgraded every page it sat on.
        html = (
            "<nav>Find My Ward Register to Vote (Elections Ontario)</nav>"
            + "".join(
                f'<a href="/media/{index}/t-2026-{index}-work.pdf">T-2026-{index}</a>'
                for index in range(1, 4)
            )
        )

        verdict = classify.classify_page(html, PAGE_URL)

        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_HIGH)

    def test_overwhelming_document_evidence_survives_gating_language(self) -> None:
        html = "<p>create an account</p>" + "".join(
            f'<a href="/media/{index}/t-2026-{index}-work.pdf">T-2026-{index}</a>'
            for index in range(1, 9)
        )

        verdict = classify.classify_page(html, PAGE_URL)

        self.assertEqual(verdict["confidence"], schema.CONFIDENCE_HIGH)


class PlatformDetectionTests(unittest.TestCase):
    def test_each_platform_maps_to_its_class(self) -> None:
        cases = (
            ("https://town.bidsandtenders.ca/x", schema.CLASS_BIDS_AND_TENDERS, "bidsandtenders"),
            ("https://www.biddingo.com/town", schema.CLASS_BIDDINGO, "biddingo"),
            ("https://town.bonfirehub.ca/portal", schema.CLASS_OTHER_PLATFORM, "bonfire"),
            ("https://www.merx.com/town", schema.CLASS_OTHER_PLATFORM, "merx"),
        )
        for url, expected_class, expected_platform in cases:
            with self.subTest(url=url):
                verdict = classify.classify_page(f'<a href="{url}">Bids</a>', PAGE_URL)
                self.assertEqual(verdict["classification"], expected_class)
                self.assertEqual(verdict["platform"], expected_platform)

    def test_a_platform_named_only_in_body_text_still_counts(self) -> None:
        verdict = classify.classify_page(
            "<p>Our tenders are posted on Biddingo.</p>", PAGE_URL
        )

        self.assertEqual(verdict["classification"], schema.CLASS_BIDDINGO)


if __name__ == "__main__":
    unittest.main()
