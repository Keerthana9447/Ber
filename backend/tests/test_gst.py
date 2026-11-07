import random
import unittest
from decimal import Decimal as D

from berev_core.gst import (GSTError, LineInput, compute_invoice, gstin_checksum_char,
                            is_valid_gstin)
from berev_core.words import amount_in_words

AP, TS = "37", "36"


def L(price, rate, qty=1, **kw):
    return LineInput("Item", "8711", qty, price, rate, **kw)


class GstTests(unittest.TestCase):
    def test_intra_state_splits_cgst_sgst(self):
        r = compute_invoice([L("100000", "18")], AP, AP)
        self.assertFalse(r.is_inter_state)
        self.assertEqual((r.cgst_total, r.sgst_total, r.igst_total), (D("9000"), D("9000"), D("0")))
        self.assertEqual(r.grand_total, D("118000"))

    def test_inter_state_uses_igst(self):
        r = compute_invoice([L("100000", "18")], AP, TS)
        self.assertTrue(r.is_inter_state)
        self.assertEqual((r.cgst_total, r.sgst_total, r.igst_total), (D("0"), D("0"), D("18000")))

    def test_discount_percent_reduces_taxable(self):
        r = compute_invoice([L("50000", "5", qty=2, discount_pct="10")], AP, AP)
        ln = r.lines[0]
        self.assertEqual((ln.gross, ln.discount, ln.taxable), (D("100000"), D("10000"), D("90000")))
        self.assertEqual((r.cgst_total, r.sgst_total, r.grand_total), (D("2250"), D("2250"), D("94500")))

    def test_discount_amount(self):
        r = compute_invoice([L("1000", "18", discount_amount="100")], AP, AP)
        self.assertEqual(r.taxable_total, D("900"))

    def test_half_up_rounding_and_round_off(self):
        r = compute_invoice([L("33.33", "5")], AP, AP)
        self.assertEqual((r.cgst_total, r.sgst_total), (D("0.83"), D("0.83")))
        self.assertEqual(r.total_before_round, D("34.99"))
        self.assertEqual((r.round_off, r.grand_total), (D("0.01"), D("35")))

    def test_negative_round_off(self):
        r = compute_invoice([L("100.40", "0")], AP, AP)
        self.assertEqual((r.round_off, r.grand_total), (D("-0.40"), D("100")))

    def test_cess(self):
        r = compute_invoice([L("100000", "28", cess_rate="1")], AP, TS)
        self.assertEqual((r.igst_total, r.cess_total), (D("28000"), D("1000")))

    def test_bill_of_supply_has_no_tax(self):
        r = compute_invoice([L("1000", "18")], AP, AP, tax_applicable=False)
        self.assertEqual(r.tax_total, D("0"))
        self.assertEqual(r.grand_total, D("1000"))

    def test_hsn_summary_groups(self):
        r = compute_invoice([L("100", "18"), L("200", "18"), L("100", "5")], AP, AP)
        self.assertEqual(len(r.hsn_summary), 2)
        row = [x for x in r.hsn_summary if x.gst_rate == 18][0]
        self.assertEqual(row.taxable, D("300"))

    def test_rejects_bad_input(self):
        bad = [L(100.0, "18"), L("100", "7"), L("100", "18", qty=0), L("-1", "18"),
               L("100", "18", discount_amount="200"),
               L("100", "18", discount_pct="10", discount_amount="5"),
               L("100", "18", qty="1.2345"),
               LineInput("x", "12", 1, "100", "18")]
        for line in bad:
            with self.subTest(line=line), self.assertRaises(GSTError):
                compute_invoice([line], AP, AP)
        with self.assertRaises(GSTError):
            compute_invoice([], AP, AP)
        with self.assertRaises(GSTError):
            compute_invoice([L("1", "5")], AP, "00")

    def test_configured_rates_are_enforced(self):
        with self.assertRaises(GSTError):
            compute_invoice([L("100", "28")], AP, AP, allowed_rates={D("5"), D("18")})

    def test_totals_always_reconcile(self):
        rnd = random.Random(7)
        for _ in range(500):
            lines = [L(f"{rnd.randint(1, 99999)}.{rnd.randint(0, 99):02d}",
                       rnd.choice(["0", "5", "12", "18", "28"]), qty=rnd.randint(1, 9),
                       discount_pct=rnd.choice(["0", "2.5", "10"]))
                     for _ in range(rnd.randint(1, 6))]
            r = compute_invoice(lines, AP, rnd.choice([AP, TS]))
            self.assertEqual(r.grand_total, r.taxable_total + r.tax_total + r.round_off)
            self.assertEqual(r.grand_total, r.grand_total.to_integral_value())
            self.assertEqual(r.cgst_total, r.sgst_total)
            self.assertTrue(abs(r.round_off) <= D("0.5"))


class GstinTests(unittest.TestCase):
    def test_valid_sample(self):
        self.assertTrue(is_valid_gstin("27AAPFU0939F1ZV"))
        self.assertTrue(is_valid_gstin("27AAPFU0939F1ZV", "27"))
        self.assertFalse(is_valid_gstin("27AAPFU0939F1ZV", "37"))

    def test_bad_checksum_and_format(self):
        self.assertFalse(is_valid_gstin("27AAPFU0939F1ZA"))
        self.assertFalse(is_valid_gstin("27AAPFU0939F1"))
        self.assertFalse(is_valid_gstin(""))
        self.assertFalse(is_valid_gstin("00AAPFU0939F1ZV"))

    def test_generated_checksum_roundtrip(self):
        first14 = "37ABCDE1234F1Z"
        self.assertTrue(is_valid_gstin(first14 + gstin_checksum_char(first14)))


class WordsTests(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(amount_in_words("118000"), "Indian Rupees One Lakh Eighteen Thousand Only")
        self.assertEqual(amount_in_words("1234.50"),
                         "Indian Rupees One Thousand Two Hundred Thirty Four and Fifty Paise Only")
        self.assertEqual(amount_in_words("0"), "Indian Rupees Zero Only")
        self.assertEqual(amount_in_words("10000000"), "Indian Rupees One Crore Only")
        self.assertEqual(amount_in_words("1500000000"),
                         "Indian Rupees One Hundred Fifty Crore Only")
        self.assertEqual(amount_in_words("0.05"), "Indian Rupees Zero and Five Paise Only")
        self.assertEqual(amount_in_words("100"), "Indian Rupees One Hundred Only")
        with self.assertRaises(TypeError):
            amount_in_words(1.5)


if __name__ == "__main__":
    unittest.main()
