import unittest

from profiles import schema, tokens


FIRM = {
    "name": "Georgian Bay Civil Ltd.",
    "trades": ["water_wastewater"],
    "regions": ["ontario_any"],
    "past_projects": [],
}


class TokenGenerationTests(unittest.TestCase):
    def test_a_token_is_long_and_url_safe(self) -> None:
        token = schema.generate_board_token()

        self.assertGreaterEqual(len(token), 32)
        self.assertRegex(token, r"^[A-Za-z0-9_-]+$")

    def test_tokens_do_not_repeat(self) -> None:
        minted = {schema.generate_board_token() for _ in range(500)}

        self.assertEqual(len(minted), 500)

    def test_the_hash_is_stable_and_one_way(self) -> None:
        token = schema.generate_board_token()

        digest = schema.board_token_hash(token)

        self.assertEqual(digest, schema.board_token_hash(token))
        self.assertEqual(len(digest), 64)
        self.assertNotIn(token, digest)

    def test_different_tokens_hash_differently(self) -> None:
        first = schema.board_token_hash(schema.generate_board_token())
        second = schema.board_token_hash(schema.generate_board_token())

        self.assertNotEqual(first, second)


class FirmTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)

    def _token(self, firm_id: int) -> str | None:
        row = self.connection.execute(
            "SELECT board_token FROM firms WHERE id = ?", (firm_id,)
        ).fetchone()
        return row["board_token"] if row else None

    def test_a_new_firm_gets_a_token_at_creation(self) -> None:
        firm_id = schema.upsert_firm(self.connection, FIRM)

        self.assertTrue(self._token(firm_id))

    def test_updating_a_firm_never_changes_its_token(self) -> None:
        # Rotating on update would silently break a link already in a customer's
        # inbox, and nothing about editing trades should invalidate access.
        firm_id = schema.upsert_firm(self.connection, FIRM)
        before = self._token(firm_id)

        schema.upsert_firm(self.connection, {**FIRM, "trades": ["roadwork"]})

        self.assertEqual(self._token(firm_id), before)

    def test_two_firms_get_different_tokens(self) -> None:
        first = schema.upsert_firm(self.connection, FIRM)
        second = schema.upsert_firm(self.connection, {**FIRM, "name": "Another Co."})

        self.assertNotEqual(self._token(first), self._token(second))

    def test_a_duplicate_token_is_refused_by_the_database(self) -> None:
        import sqlite3

        first = schema.upsert_firm(self.connection, FIRM)
        schema.upsert_firm(self.connection, {**FIRM, "name": "Another Co."})

        with self.assertRaises(sqlite3.IntegrityError):
            with self.connection:
                self.connection.execute(
                    "UPDATE firms SET board_token = ? WHERE name = 'Another Co.'",
                    (self._token(first),),
                )


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = schema.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.firm_id = schema.upsert_firm(self.connection, FIRM)

    def _clear_token(self, firm_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE firms SET board_token = NULL WHERE id = ?", (firm_id,)
            )

    def test_backfill_mints_for_firms_without_a_token(self) -> None:
        self._clear_token(self.firm_id)

        minted = tokens.backfill(self.connection)

        self.assertEqual(len(minted), 1)
        self.assertEqual(minted[0]["id"], self.firm_id)
        self.assertTrue(minted[0]["token"])

    def test_backfill_is_idempotent(self) -> None:
        self._clear_token(self.firm_id)
        tokens.backfill(self.connection)

        again = tokens.backfill(self.connection)

        self.assertEqual(again, [])

    def test_backfill_leaves_existing_tokens_alone(self) -> None:
        before = self.connection.execute(
            "SELECT board_token FROM firms WHERE id = ?", (self.firm_id,)
        ).fetchone()["board_token"]

        tokens.backfill(self.connection)

        after = self.connection.execute(
            "SELECT board_token FROM firms WHERE id = ?", (self.firm_id,)
        ).fetchone()["board_token"]
        self.assertEqual(after, before)

    def test_rotation_replaces_a_token_and_says_so(self) -> None:
        before = tokens.board_tokens(self.connection)[0]["token"]

        with self.assertLogs("profiles.tokens", level="WARNING"):
            after = tokens.rotate(self.connection, self.firm_id)

        self.assertNotEqual(after, before)

    def test_rotating_an_unknown_firm_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tokens.rotate(self.connection, 999)

    def test_board_paths_are_listed_with_their_hashes(self) -> None:
        entries = tokens.board_tokens(self.connection)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertTrue(entry["path"].startswith("/board/"))
        self.assertIn(entry["token"], entry["path"])
        self.assertEqual(entry["hash"], schema.board_token_hash(entry["token"]))


if __name__ == "__main__":
    unittest.main()
