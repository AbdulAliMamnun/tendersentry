import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from extract import pipeline


class EnvLoadingTests(unittest.TestCase):
    def test_loads_quotes_whitespace_comments_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                """
                # ignored
                OPENAI_API_KEY = \"sk-test-value\"  # trailing comment
                export SECOND_VALUE = ' spaced value '
                UNQUOTED = useful-value  # trailing comment
                """,
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "",
                    "SECOND_VALUE": "",
                    "UNQUOTED": "",
                },
            ):
                loaded = pipeline._load_env_file(env_path)

                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test-value")
                self.assertEqual(os.environ["SECOND_VALUE"], " spaced value ")
                self.assertEqual(os.environ["UNQUOTED"], "useful-value")
                self.assertEqual(
                    loaded, {"OPENAI_API_KEY", "SECOND_VALUE", "UNQUOTED"}
                )

    def test_supports_existing_bare_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text("sk-test-bare-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                with self.assertLogs(pipeline.LOGGER, logging.WARNING):
                    loaded = pipeline._load_env_file(env_path)

                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test-bare-key")
                self.assertEqual(loaded, {"OPENAI_API_KEY"})

    def test_environment_wins_and_log_masks_after_six_characters(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-secret-value"}):
            with patch.object(pipeline, "_API_KEY_SOURCE_LOGGED", False):
                with self.assertLogs(pipeline.LOGGER, logging.INFO) as messages:
                    self.assertEqual(
                        pipeline._require_api_key(), "sk-env-secret-value"
                    )

        output = "\n".join(messages.output)
        self.assertIn("found in environment: sk-env...", output)
        self.assertNotIn("secret-value", output)

    def test_default_env_path_is_anchored_to_pipeline_repository(self) -> None:
        self.assertEqual(
            pipeline.REPO_ROOT,
            Path(pipeline.__file__).resolve().parents[1],
        )


if __name__ == "__main__":
    unittest.main()
