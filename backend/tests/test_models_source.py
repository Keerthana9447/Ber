import unittest
import re

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import configure_mappers

from app.models import Base
from scripts.generate_models import (
    CONSTRAINT_START,
    OUTPUT,
    TABLE_START,
    build,
    column_definition,
    split_top_level,
)


class GeneratedModelTests(unittest.TestCase):
    def test_models_regenerate_identically_from_schema_source(self):
        self.assertEqual(OUTPUT.read_text(encoding="utf-8"), build())

    def test_all_schema_tables_are_mapped(self):
        self.assertEqual(len(Base.metadata.tables), 49)
        self.assertIn("users", Base.metadata.tables)
        self.assertIn("audit_logs", Base.metadata.tables)

    def test_all_source_columns_are_present_in_mapped_tables(self):
        source = re.sub(r"--[^\r\n]*", "", (OUTPUT.parents[1] / "db" / "schema.sql").read_text(encoding="utf-8"))
        for match in TABLE_START.finditer(source):
            expected = set()
            for segment in split_top_level(match.group(2)):
                if segment and not CONSTRAINT_START.match(segment):
                    _, column_name, _ = column_definition(segment)
                    expected.add(column_name)
            actual = set(Base.metadata.tables[match.group(1)].columns.keys())
            self.assertEqual(actual, expected, match.group(1))

    def test_schema_indexes_are_represented_in_metadata(self):
        source = (OUTPUT.parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
        expected = len(re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", source, re.I))
        actual = sum(len(table.indexes) for table in Base.metadata.tables.values())
        self.assertEqual(actual, expected)

    def test_schema_constraints_and_relationships_are_mapped(self):
        source = re.sub(r"--[^\r\n]*", "", (OUTPUT.parents[1] / "db" / "schema.sql").read_text(encoding="utf-8"))
        blocks = list(TABLE_START.finditer(source))
        expected_checks = sum(len(re.findall(r"\bCHECK\s*\(", match.group(2), re.I)) for match in blocks)
        expected_unique = sum(len(re.findall(r"\bUNIQUE\b", match.group(2), re.I)) for match in blocks)
        expected_foreign_keys = sum(len(re.findall(r"\bREFERENCES\b", match.group(2), re.I)) for match in blocks)
        expected_foreign_keys += len(re.findall(r"ALTER\s+TABLE\b.*?\bFOREIGN\s+KEY\b", source, re.I | re.S))

        actual_checks = sum(
            isinstance(constraint, CheckConstraint)
            for table in Base.metadata.tables.values()
            for constraint in table.constraints
        )
        actual_unique = sum(
            isinstance(constraint, UniqueConstraint)
            for table in Base.metadata.tables.values()
            for constraint in table.constraints
        )
        actual_foreign_keys = sum(
            len(table.foreign_keys) for table in Base.metadata.tables.values()
        )
        self.assertEqual(actual_checks, expected_checks)
        self.assertEqual(actual_unique, expected_unique)
        self.assertEqual(actual_foreign_keys, expected_foreign_keys)
        configure_mappers()


if __name__ == "__main__":
    unittest.main()
