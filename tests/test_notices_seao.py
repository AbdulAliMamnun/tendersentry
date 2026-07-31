import json
import unittest
from pathlib import Path

from notices import db, seao


FIXTURES = Path(__file__).resolve().parent / "fixtures"
RELEASES_FIXTURE = FIXTURES / "seao_releases.json"
PACKAGE_FIXTURE = FIXTURES / "seao_package.json"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


class _FakeResponse:
    def __init__(self, payload: dict | None = None, chunks: list[bytes] | None = None):
        self._payload = payload or {}
        self._chunks = chunks or [b"{}"]

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload

    def iter_content(self, chunk_size: int = 0):
        return iter(self._chunks)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args) -> None:
        return None


class _FakeSession:
    """Stand in for ``requests``, recording calls and replaying fixtures.

    ``bodies`` maps a filename to the bytes served for it; ``body`` is the default
    for any file not named there.
    """

    def __init__(
        self,
        payload: dict | None = None,
        body: bytes = b"{}",
        bodies: dict[str, bytes] | None = None,
    ) -> None:
        self.payload = payload or {}
        self.body = body
        self.bodies = bodies or {}
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        filename = url.rsplit("/", 1)[-1]
        return _FakeResponse(self.payload, [self.bodies.get(filename, self.body)])


class WeeklyResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = _load(PACKAGE_FIXTURE)

    def test_weekly_files_are_sorted_newest_first_by_filename_date(self) -> None:
        resources = seao.weekly_resources(self.payload)

        self.assertEqual(
            [resource["name"] for resource in resources],
            [
                "hebdo_20260720_20260726.json",
                "hebdo_20260713_20260719.json",
                "hebdo_20260706_20260712.json",
            ],
        )

    def test_monthly_files_and_duplicate_filenames_are_excluded(self) -> None:
        resources = seao.weekly_resources(self.payload)

        names = [resource["name"] for resource in resources]
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse(any("mensuel" in name for name in names))

    def test_an_empty_package_yields_no_resources(self) -> None:
        self.assertEqual(seao.weekly_resources({}), [])
        self.assertEqual(seao.weekly_resources({"result": {"resources": []}}), [])

    def test_discovery_calls_the_ckan_api_with_the_dataset_id(self) -> None:
        session = _FakeSession(self.payload)

        resources = seao.discover_weekly_resources(session)

        self.assertEqual(len(resources), 3)
        self.assertEqual(session.calls[0]["params"], {"id": seao.config.SEAO_PACKAGE_ID})


class ParseReleasesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = seao.parse_releases(_load(RELEASES_FIXTURE))
        cls.by_id = {record["source_id"]: record for record in cls.records}

    def test_every_fixture_release_becomes_one_record(self) -> None:
        self.assertEqual(len(self.records), 3)
        self.assertTrue(all(record["source"] == "seao" for record in self.records))

    def test_an_active_works_notice_is_fully_normalized(self) -> None:
        record = self.by_id["ocds-ec9k95-20004970"]

        self.assertEqual(record["title"], "Rénovation intérieure salle municipale")
        self.assertEqual(record["buyer_name"], "Municipalité de Saint-Majorique")
        self.assertEqual(record["buyer_type"], "municipal")
        self.assertEqual(record["category_raw"], "Travaux de construction")
        self.assertEqual(record["category_normalized"], "construction")
        self.assertEqual(record["region"], "QC")
        self.assertEqual(record["closing_date"], "2026-08-27T11:00:00-04:00")
        self.assertEqual(record["posted_date"], "2026-07-20T09:45:38-04:00")
        self.assertEqual(record["status"], "open")
        self.assertTrue(record["notice_url"].startswith("https://seao.gouv.qc.ca/"))

    def test_a_published_tender_value_is_captured_with_its_currency(self) -> None:
        record = self.by_id["ocds-ec9k95-20160595"]

        self.assertEqual(record["estimated_value"], 653199.0)
        self.assertEqual(record["currency"], "CAD")

    def test_releases_without_a_value_leave_the_column_null(self) -> None:
        record = self.by_id["ocds-ec9k95-20004970"]

        self.assertIsNone(record["estimated_value"])
        self.assertIsNone(record["currency"])

    def test_a_contract_tagged_release_is_promoted_to_awarded(self) -> None:
        self.assertEqual(self.by_id["ocds-ec9k95-20085214"]["status"], "awarded")

    def test_active_tender_releases_stay_open(self) -> None:
        self.assertEqual(self.by_id["ocds-ec9k95-20004970"]["status"], "open")
        self.assertEqual(self.by_id["ocds-ec9k95-20160595"]["status"], "open")

    def test_seao_own_municipal_flag_drives_the_buyer_type(self) -> None:
        self.assertEqual(self.by_id["ocds-ec9k95-20004970"]["buyer_type"], "municipal")
        self.assertEqual(self.by_id["ocds-ec9k95-20085214"]["buyer_type"], "health")

    def test_seao_documents_are_never_marked_openly_available(self) -> None:
        for record in self.records:
            self.assertFalse(record["documents_open"])

    def test_a_malformed_payload_is_survivable(self) -> None:
        with self.assertLogs("notices.seao", level="WARNING"):
            self.assertEqual(seao.parse_releases({"releases": "nope"}), [])
        self.assertEqual(seao.parse_releases({"releases": [None, {}, {"tender": {}}]}), [])

    def test_the_newest_release_for_one_procurement_wins(self) -> None:
        payload = {
            "releases": [
                {
                    "ocid": "ocds-ec9k95-1",
                    "date": "2026-07-22T10:00:00-04:00",
                    "tag": ["tenderUpdate"],
                    "tender": {"title": "Second state", "status": "cancelled"},
                },
                {
                    "ocid": "ocds-ec9k95-1",
                    "date": "2026-07-20T10:00:00-04:00",
                    "tag": ["tender"],
                    "tender": {"title": "First state", "status": "active"},
                },
            ]
        }

        records = seao.parse_releases(payload)

        self.assertEqual([record["title"] for record in records], ["First state", "Second state"])


class IngestWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)

    def test_a_cold_table_backfills_twelve_weeks(self) -> None:
        self.assertEqual(seao._default_window(self.connection), seao.BACKFILL_WEEKS)
        self.assertEqual(seao.BACKFILL_WEEKS, 12)

    def test_a_populated_table_uses_the_rolling_four_week_window(self) -> None:
        db.upsert_notices(
            self.connection,
            [{"source": "seao", "source_id": "ocds-1", "title": "Stored"}],
        )

        self.assertEqual(seao._default_window(self.connection), seao.DEFAULT_WEEKS)
        self.assertEqual(seao.DEFAULT_WEEKS, 4)

    def test_ingest_selects_the_requested_number_of_weeks_oldest_first(self) -> None:
        payload = _load(PACKAGE_FIXTURE)
        body = RELEASES_FIXTURE.read_bytes()
        session = _FakeSession(payload, body)
        cache_dir = Path(self.enterContext(_temporary_directory()))

        result = seao.ingest(
            self.connection, weeks=2, cache_dir=cache_dir, session=session
        )

        downloaded = [
            call["url"].rsplit("/", 1)[-1]
            for call in session.calls
            if call["url"].endswith(".json")
        ]
        self.assertEqual(
            downloaded,
            ["hebdo_20260713_20260719.json", "hebdo_20260720_20260726.json"],
        )
        self.assertEqual(result["inserted"], 3)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 0)
        self.assertEqual(result["notes"], [])

    def test_a_procurement_spanning_two_weeks_is_written_once_at_its_newest_state(
        self,
    ) -> None:
        older = json.dumps(
            {
                "releases": [
                    {
                        "ocid": "ocds-ec9k95-1",
                        "date": "2026-07-15T10:00:00-04:00",
                        "tag": ["tender"],
                        "tender": {
                            "title": "Réfection de la rue Principale",
                            "status": "active",
                            "tenderPeriod": {"endDate": "2026-09-30T11:00:00-04:00"},
                        },
                    }
                ]
            }
        ).encode()
        newer = json.dumps(
            {
                "releases": [
                    {
                        "ocid": "ocds-ec9k95-1",
                        "date": "2026-07-22T10:00:00-04:00",
                        "tag": ["tenderCancellation"],
                        "tender": {
                            "title": "Réfection de la rue Principale",
                            "status": "cancelled",
                            "tenderPeriod": {"endDate": "2026-09-30T11:00:00-04:00"},
                        },
                    }
                ]
            }
        ).encode()
        session = _FakeSession(
            _load(PACKAGE_FIXTURE),
            bodies={
                "hebdo_20260713_20260719.json": older,
                "hebdo_20260720_20260726.json": newer,
            },
        )
        cache_dir = Path(self.enterContext(_temporary_directory()))

        first = seao.ingest(
            self.connection, weeks=2, cache_dir=cache_dir, session=session
        )
        second = seao.ingest(
            self.connection, weeks=2, cache_dir=cache_dir, session=session
        )

        self.assertEqual(first["parsed"], 2)
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(first["updated"], 0)
        self.assertEqual(
            db.sample_rows(self.connection, "seao")[0]["status"], "cancelled"
        )
        # The corpus did not change, so the second run must report no writes at all.
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 1)

    def test_cached_weekly_files_are_not_downloaded_again(self) -> None:
        payload = _load(PACKAGE_FIXTURE)
        session = _FakeSession(payload, RELEASES_FIXTURE.read_bytes())
        cache_dir = Path(self.enterContext(_temporary_directory()))

        first = seao.fetch_weekly_file(
            {
                "name": "hebdo_20260720_20260726.json",
                "url": "https://example.invalid/hebdo_20260720_20260726.json",
            },
            cache_dir,
            session,
        )
        second = seao.fetch_weekly_file(
            {
                "name": "hebdo_20260720_20260726.json",
                "url": "https://example.invalid/hebdo_20260720_20260726.json",
            },
            cache_dir,
            session,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(session.calls), 1)


def _temporary_directory():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
