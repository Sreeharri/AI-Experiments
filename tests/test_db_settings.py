import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from eurlex_enrichment.inputs import postgres_dsn


class DatabaseSettingsTests(unittest.TestCase):
    def test_explicit_file_preserves_database_and_ssl(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                'DATABASE_URL="postgresql+psycopg://demo:fake%23@host:5432/legalradar?sslmode=require"\n',
                encoding="utf-8-sig",
            )
            with patch.dict(os.environ, {
                "LEGAL_RADAR_DATABASE_URL": "postgresql://wrong/wrong",
                "PGDATABASE": "PGSQLPROD01",
            }):
                self.assertEqual(
                    postgres_dsn(env_file),
                    "postgresql://demo:fake%23@host:5432/legalradar?sslmode=require",
                )

    def test_file_without_database_url_fails_before_connecting(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OTHER_SETTING=1\n", encoding="utf-8")
            with patch.dict(os.environ, {"LEGAL_RADAR_DATABASE_URL": "postgresql://wrong/wrong"}):
                with self.assertRaisesRegex(ValueError, "Set DATABASE_URL"):
                    postgres_dsn(env_file)
