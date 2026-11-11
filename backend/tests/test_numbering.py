import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

from berev_core.numbering import (fy_label, financial_year_start, format_invoice_number,
                                  next_sequence, series_key)


def connect(path):
    c = sqlite3.connect(path, timeout=60, isolation_level=None)  # manual transactions
    return c


class FormatTests(unittest.TestCase):
    def test_financial_year_boundaries(self):
        self.assertEqual(financial_year_start(date(2026, 3, 31)), 2025)
        self.assertEqual(financial_year_start(date(2026, 4, 1)), 2026)
        self.assertEqual(fy_label(date(2026, 11, 5)), "26-27")
        self.assertEqual(fy_label(date(2027, 1, 5), short=False), "2026-27")

    def test_format_and_length_rule(self):
        n = format_invoice_number("INV", date(2026, 11, 5), 1)
        self.assertEqual(n, "INV/26-27/00001")
        self.assertLessEqual(len(n), 16)
        with self.assertRaises(ValueError):
            format_invoice_number("BEREV-INVOICE", date(2026, 11, 5), 1)
        with self.assertRaises(ValueError):
            format_invoice_number("IN V", date(2026, 11, 5), 1)
        with self.assertRaises(ValueError):
            format_invoice_number("INV", date(2026, 11, 5), 0)

    def test_series_resets_each_fy(self):
        a = series_key("tax_invoice", "INV", date(2026, 3, 31))
        b = series_key("tax_invoice", "INV", date(2026, 4, 1))
        self.assertNotEqual(a, b)


class AllocatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # cleanups run LIFO: registered first => runs LAST, after every connection is closed
        # (Windows cannot delete a SQLite file that still has an open connection).
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "t.db")
        c = self.conn()
        c.executescript("""
            CREATE TABLE number_sequences (series TEXT PRIMARY KEY, last_number INTEGER NOT NULL);
            CREATE TABLE invoices (id INTEGER PRIMARY KEY, invoice_number TEXT NOT NULL UNIQUE);
        """)

    def conn(self):
        c = connect(self.path)
        self.addCleanup(c.close)
        return c

    def test_concurrent_issuance_has_no_duplicates_or_gaps(self):
        series = series_key("tax_invoice", "INV", date(2026, 11, 5))
        errors, threads = [], []

        def worker():
            c = connect(self.path)
            try:
                for _ in range(10):
                    c.execute("BEGIN IMMEDIATE")
                    cur = c.cursor()
                    seq = next_sequence(cur, series, "qmark")
                    cur.execute("INSERT INTO invoices (invoice_number) VALUES (?)",
                                (format_invoice_number("INV", date(2026, 11, 5), seq),))
                    c.execute("COMMIT")
            except Exception as e:  # noqa
                errors.append(e)
            finally:
                c.close()

        for _ in range(12):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        c = self.conn()
        nums = [r[0] for r in c.execute("SELECT invoice_number FROM invoices")]
        self.assertEqual(len(nums), 120)
        self.assertEqual(len(set(nums)), 120)
        self.assertEqual(c.execute("SELECT last_number FROM number_sequences").fetchone()[0], 120)
        seqs = sorted(int(n.rsplit("/", 1)[1]) for n in nums)
        self.assertEqual(seqs, list(range(1, 121)))

    def test_rollback_leaves_no_gap(self):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        self.assertEqual(next_sequence(c.cursor(), "s", "qmark"), 1)
        c.execute("ROLLBACK")
        c.execute("BEGIN IMMEDIATE")
        self.assertEqual(next_sequence(c.cursor(), "s", "qmark"), 1)
        c.execute("COMMIT")
        c.execute("BEGIN IMMEDIATE")
        self.assertEqual(next_sequence(c.cursor(), "s", "qmark"), 2)
        c.execute("COMMIT")

    def test_unique_constraint_is_last_line_of_defence(self):
        c = self.conn()
        c.execute("INSERT INTO invoices (invoice_number) VALUES ('INV/26-27/00001')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO invoices (invoice_number) VALUES ('INV/26-27/00001')")


if __name__ == "__main__":
    unittest.main()
