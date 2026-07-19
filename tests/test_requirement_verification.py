import unittest

from extract.pipeline import verify_requirements


class RequirementVerificationTests(unittest.TestCase):
    def test_page_repair_persists_original_page_number(self) -> None:
        requirements = [
            {
                "id": "T-R001",
                "source_file": "tender.pdf",
                "page_number": 8,
                "verbatim_quote": "Submit the completed bid form.",
            }
        ]
        pages = {
            "tender.pdf": {
                "8": "Other text.",
                "12": "Submit the completed bid form.",
            }
        }

        verified, dropped = verify_requirements(requirements, pages)

        self.assertEqual(dropped, [])
        self.assertEqual(verified[0]["original_page_number"], 8)
        self.assertEqual(verified[0]["page_number"], 12)
        self.assertEqual(verified[0]["verification_status"], "page_repaired")

    def test_verified_page_does_not_add_original_page_number(self) -> None:
        requirements = [
            {
                "id": "T-R001",
                "source_file": "tender.pdf",
                "page_number": 8,
                "verbatim_quote": "Submit the completed bid form.",
            }
        ]
        pages = {"tender.pdf": {"8": "Submit the completed bid form."}}

        verified, dropped = verify_requirements(requirements, pages)

        self.assertEqual(dropped, [])
        self.assertNotIn("original_page_number", verified[0])
        self.assertEqual(verified[0]["verification_status"], "verified")


if __name__ == "__main__":
    unittest.main()
