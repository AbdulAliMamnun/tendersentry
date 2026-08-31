"""The curated blocker: how it is produced, and what the export does without it.

The daily job failed at `export_demo_board` on every run, because `select_blocker()`
globbed `data/tenders/` — a gitignored directory that has never existed on a runner.
The board refused to emit without a red row, correctly, and the run died.

`tests/test_export_demo_board.py` did not catch this: it stages a fake tender directory
and patches `TENDERS_DIR` at it, so it exercises a state the runner is never in. The
runner's only state is *no extraction data at all*, and that is what this module tests.

Two properties, and the second is the one that makes the artifact worth having:

* The export works from the committed artifact with `data/tenders/` absent, and fails
  loudly — never silently blockerless — when the artifact is gone or malformed.
* `--refresh-blocker` cannot produce an artifact from an unverified quote. It re-reads
  the source PDF and re-runs the same exact-substring match the extraction pipeline
  uses, and refuses on a quote that is missing *or* that verifies somewhere other than
  where the requirement says it does. An artifact that could be written from a quote
  nobody checked would be worth less than no artifact, because it would look the same.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from matchrec import schema as matchrec_schema
from notices import db
from profiles import schema as profiles_schema
from scripts import export_demo_board


ARTIFACT = Path(config.PROJECT_ROOT) / "web" / "data" / "demo-blocker.json"
TENDERS = Path(config.PROJECT_ROOT) / config.DATA_DIR

FIRM = {
    "name": "Georgian Bay Civil Ltd.",
    "trades": ["water_wastewater"],
    "regions": ["ontario_any"],
    "submission_capabilities": ["email", "portal"],
    "buyer_type_preferences": ["municipal"],
    "past_projects": [],
}

VALID = {
    "artifact_version": export_demo_board.BLOCKER_ARTIFACT_VERSION,
    "generated_at": "2026-08-31T02:04:44+00:00",
    "verified_at": "2026-08-31T02:04:44+00:00",
    "extracted_at": "2026-07-19T22:07:29+00:00",
    "tender_id": "cb-757-46105229",
    "title": "RFSO 5P300-26-0001",
    "buyer": "",
    "requirement_id": "cb-757-46105229-R009",
    "requirement_text": "Submit offers by fax to 1-877-558-2349.",
    "quote": "The only acceptable facsimile for responses to the RFSO is 1-877-558-2349.",
    "page": 2,
    "source_file": "rfso-5p300-26-0001-a.pdf",
    "source_sha256": "8fa861bb9d5ecfaa5c7662c3116cad8fb3463f4b2372b7c399737acf0582646f",
    "check_field": "submission_method",
    "check_value": "1-877-558-2349",
}


def _database(path: Path):
    connection = matchrec_schema.connect(path)
    profiles_schema.create_schema(connection)
    db.migrate_scale_columns(connection)
    profiles_schema.upsert_firm(connection, FIRM)
    return connection


class ExportWithoutExtractionDataTests(unittest.TestCase):
    """The runner's only state: no data/tenders/ at all."""

    def setUp(self) -> None:
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db_path = self.temp / "test.db"
        _database(self.db_path).close()
        self.out = self.temp / "out"
        self.out.mkdir()
        self.artifact = self.temp / "demo-blocker.json"
        self.artifact.write_text(json.dumps(VALID), encoding="utf-8")
        # No extraction data anywhere, exactly as on a fresh checkout.
        self.enterContext(
            patch.object(export_demo_board, "TENDERS_DIR", self.temp / "absent")
        )
        self.enterContext(
            patch.object(export_demo_board, "BLOCKER_PATH", self.artifact)
        )

    def test_the_export_succeeds_from_the_artifact_alone(self) -> None:
        written = export_demo_board.export(
            firm_id=1, out_dir=self.out, db_path=self.db_path
        )
        board = json.loads(written["demo-board.json"].read_text(encoding="utf-8"))
        self.assertEqual("cb-757-46105229", board["blocker"]["tender_id"])
        self.assertIn("facsimile", board["blocker"]["quote"])
        self.assertEqual(2, board["blocker"]["page"])

    def test_the_board_carries_the_provenance_the_card_displays(self) -> None:
        written = export_demo_board.export(
            firm_id=1, out_dir=self.out, db_path=self.db_path
        )
        blocker = json.loads(
            written["demo-board.json"].read_text(encoding="utf-8")
        )["blocker"]
        for field in ("extracted_at", "source_file", "source_sha256", "reason"):
            self.assertTrue(blocker.get(field), f"missing {field}")

    def test_a_missing_artifact_fails_loudly_and_names_the_fix(self) -> None:
        """Never a board with no red row. That would hide a broken build."""
        self.artifact.unlink()
        with self.assertRaises(export_demo_board.BlockerArtifactMissing) as caught:
            export_demo_board.export(firm_id=1, out_dir=self.out, db_path=self.db_path)
        self.assertIn("--refresh-blocker", str(caught.exception))

    def test_a_malformed_artifact_fails_loudly(self) -> None:
        self.artifact.write_text("{not json", encoding="utf-8")
        with self.assertRaises(export_demo_board.BlockerArtifactMissing):
            export_demo_board.export(firm_id=1, out_dir=self.out, db_path=self.db_path)

    def test_an_artifact_missing_the_quote_is_refused(self) -> None:
        for field in ("quote", "page", "source_sha256", "extracted_at"):
            payload = dict(VALID)
            payload.pop(field)
            self.artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(
                export_demo_board.BlockerArtifactMissing, msg=f"accepted without {field}"
            ) as caught:
                export_demo_board.export(
                    firm_id=1, out_dir=self.out, db_path=self.db_path
                )
            self.assertIn(field, str(caught.exception))

    def test_an_artifact_from_a_future_shape_is_refused(self) -> None:
        payload = dict(VALID)
        payload["artifact_version"] = export_demo_board.BLOCKER_ARTIFACT_VERSION + 1
        self.artifact.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(export_demo_board.BlockerArtifactMissing):
            export_demo_board.export(firm_id=1, out_dir=self.out, db_path=self.db_path)


class RefreshVerificationTests(unittest.TestCase):
    """--refresh-blocker must not be able to bless an unverified quote."""

    def setUp(self) -> None:
        if not TENDERS.is_dir():
            self.skipTest("data/tenders/ is absent (a runner, or a fresh clone)")
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db_path = self.temp / "test.db"
        _database(self.db_path).close()

    def test_a_quote_absent_from_the_pdf_is_refused(self) -> None:
        fake = {
            "tender_id": "cb-757-46105229",
            "requirement_text": "Fabricated.",
            "quote": "This sentence does not appear anywhere in the source document.",
            "page": 2,
            "check_field": None,
            "check_value": None,
        }
        with patch.object(export_demo_board, "select_blocker", return_value=fake):
            with self.assertRaises(export_demo_board.BlockerArtifactMissing) as caught:
                export_demo_board.build_blocker_artifact()
        # It fails at the requirement lookup or the quote search; either way it is the
        # unverified quote that stops it, and it never writes.
        self.assertTrue(
            "does not appear" in str(caught.exception)
            or "carries the selected quote" in str(caught.exception),
            str(caught.exception),
        )

    def test_the_real_blocker_verifies_against_its_pdf(self) -> None:
        """The positive case, re-read from the PDF rather than from pages.json."""
        payload = export_demo_board.build_blocker_artifact()
        self.assertEqual("cb-757-46105229", payload["tender_id"])
        self.assertEqual(2, payload["page"])
        self.assertEqual("rfso-5p300-26-0001-a.pdf", payload["source_file"])
        self.assertEqual(64, len(payload["source_sha256"]))

    def test_the_shipped_artifact_still_matches_its_pdf(self) -> None:
        """The hash is the point: it keeps the claim checkable against the document."""
        shipped = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        pdf = TENDERS / shipped["tender_id"] / "raw" / shipped["source_file"]
        if not pdf.is_file():
            self.skipTest("source PDF not present")
        self.assertEqual(
            shipped["source_sha256"],
            export_demo_board.file_sha256(pdf),
            "the shipped blocker's hash no longer matches the PDF it cites",
        )


class ShippedArtifactTests(unittest.TestCase):
    """The committed artifact must satisfy the loader that reads it."""

    def test_it_loads(self) -> None:
        payload = export_demo_board.load_blocker(ARTIFACT)
        self.assertEqual("cb-757-46105229", payload["tender_id"])

    def test_it_carries_every_required_field(self) -> None:
        payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        for field in export_demo_board.REQUIRED_BLOCKER_FIELDS:
            self.assertTrue(payload.get(field), f"missing {field}")

    def test_the_export_no_longer_reads_the_gitignored_directory(self) -> None:
        """The regression itself: build_board must not touch data/tenders/."""
        source = (
            Path(config.PROJECT_ROOT) / "scripts" / "export_demo_board.py"
        ).read_text(encoding="utf-8")
        build_board = source.split("def build_board(")[1].split("\ndef ")[0]
        self.assertNotIn("select_blocker", build_board)
        self.assertNotIn("TENDERS_DIR", build_board)
        self.assertIn("load_blocker", build_board)


if __name__ == "__main__":
    unittest.main()
