import unittest

from ui.app import (
    _bid_security_text,
    _closing_board_text,
    _index_notices,
)


class UiHelperTests(unittest.TestCase):
    def test_notice_index_matches_filesystem_safe_tender_id(self) -> None:
        notice = {
            "tender_id": "PW-/NSS-006-28191",
            "title": "Construction notice",
            "closing_date": "2026-07-20T14:00:00",
        }

        indexed = _index_notices([notice])

        self.assertIs(indexed["PW-_NSS-006-28191"], notice)

    def test_past_closing_is_labeled_closed(self) -> None:
        text, css_class = _closing_board_text("2020-01-02T14:00:00")

        self.assertEqual(text, "Closed Thu Jan 2, 2020")
        self.assertEqual(css_class, "estimator-closed")

    def test_missing_bid_security_is_omitted(self) -> None:
        tender = {
            "requirements": [
                {
                    "phase": "bid_phase_mandatory",
                    "category": "submission",
                }
            ]
        }

        self.assertIsNone(_bid_security_text(tender))


if __name__ == "__main__":
    unittest.main()
