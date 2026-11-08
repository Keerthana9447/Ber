from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.database_test_config import (
    configured_test_database_url,
    database_tests_required,
    require_test_database_url,
)


class DatabaseTestConfigurationTests(unittest.TestCase):
    def test_reads_test_url_from_env_file_without_falling_back_to_dev_url(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "DATABASE_URL=postgresql+psycopg://localhost/berev_dev\n"
                "TEST_DATABASE_URL=postgresql+psycopg://localhost/berev_test\n",
                encoding="utf-8",
            )
            result = configured_test_database_url({}, env_file)
        self.assertTrue(result.endswith("/berev_test"))

    def test_environment_override_must_still_target_test_database(self):
        with self.assertRaisesRegex(RuntimeError, "only to berev_test"):
            configured_test_database_url(
                {"BEREV_TEST_DATABASE_URL": "postgresql+psycopg://localhost/berev_dev"},
                Path("missing.env"),
            )

    def test_required_mode_rejects_missing_test_database_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "no BEREV_TEST_DATABASE_URL"):
            require_test_database_url({}, Path("missing.env"))

    def test_required_mode_flag_is_parsed_explicitly(self):
        self.assertTrue(database_tests_required({"REQUIRE_DB_TESTS": "1"}))
        self.assertFalse(database_tests_required({"REQUIRE_DB_TESTS": "0"}))


if __name__ == "__main__":
    unittest.main()
