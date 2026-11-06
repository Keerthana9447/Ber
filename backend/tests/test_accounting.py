import unittest
from datetime import date
from decimal import Decimal as D

from berev_core import accounting as A
from berev_core.gst import LineInput, compute_invoice
from berev_core.payments import (OpenInvoice, PaymentError, allocate_fifo, invoice_status,
                                 validate_allocations)

T = date(2026, 11, 5)
AP = "37"


def inv(price, rate, qty):
    return compute_invoice([LineInput("EV Scooter", "8711", qty, price, rate)], AP, AP)


class PostingTests(unittest.TestCase):
    def test_unbalanced_entry_rejected(self):
        with self.assertRaises(A.AccountingError):
            A.JournalEntry(T, "X", "bad", [A.Line("1000", D("10"), D("0")), A.Line("4000", D("0"), D("9"))])
        with self.assertRaises(A.AccountingError):
            A.JournalEntry(T, "X", "bad", [A.Line("1000", D("10"), D("10")), A.Line("4000", D("0"), D("0"))])
        with self.assertRaises(A.AccountingError):
            A.JournalEntry(T, "X", "bad", [A.Line("9999", D("1"), D("0")), A.Line("4000", D("0"), D("1"))])

    def test_every_poster_balances_incl_round_off(self):
        i = compute_invoice([LineInput("x", "8711", 1, "33.33", "5")], AP, "36")
        for e in A.post_sales_invoice(T, "S1", i, cogs=D("10")):
            e.validate()
        A.post_credit_note(T, "C1", i, cogs_reversal=D("10"))
        A.post_purchase_invoice(T, "P1", i)
        A.post_customer_receipt(T, "R1", "10", "cash", advance=True)
        A.post_supplier_payment(T, "SP1", "10", "bank")
        A.post_expense(T, "E1", "5100", "10", "cash")

    def test_reversal_nets_to_zero(self):
        e = A.post_sales_invoice(T, "S1", inv("90000", "5", 2))[0]
        r = A.reverse_entry(e, T, "wrong customer")
        self.assertEqual(r.reverses, "S1")
        self.assertEqual(A.trial_balance([e, r])["rows"], [])

    def test_end_to_end_business_scenario(self):
        entries = []
        purchase = inv("60000", "5", 10)                    # 600000 + 30000 GST
        entries.append(A.post_purchase_invoice(T, "PI-1", purchase))
        sale = inv("90000", "5", 2)                         # 180000 + 9000 GST
        entries += A.post_sales_invoice(T, "INV/26-27/00001", sale, cogs=D("120000"))
        entries.append(A.post_customer_receipt(T, "RC-1", "100000", "bank"))
        entries.append(A.post_customer_receipt(T, "RC-2", "89000", "cash"))
        entries.append(A.post_expense(T, "EX-1", "5100", "20000", "cash"))

        self.assertEqual(sale.grand_total, D("189000"))
        self.assertEqual(A.account_balance(entries, A.AR), D("0"))          # fully collected
        self.assertEqual(A.account_balance(entries, A.INVENTORY), D("480000"))
        self.assertEqual(A.account_balance(entries, A.AP), D("-630000"))    # credit balance owed
        self.assertEqual(A.account_balance(entries, "2100"), D("-4500"))
        self.assertEqual(A.account_balance(entries, "1300"), D("15000"))
        tb = A.trial_balance(entries)
        self.assertEqual(tb["total_debit"], tb["total_credit"])
        pl = A.profit_and_loss(entries)
        self.assertEqual(pl["net_revenue"], D("180000"))
        self.assertEqual(pl["cogs"], D("120000"))
        self.assertEqual(pl["gross_profit"], D("60000"))
        self.assertEqual(pl["operating_expenses"], D("20000"))
        self.assertEqual(pl["net_profit_before_income_tax"], D("40000"))

    def test_credit_note_reduces_revenue(self):
        sale = inv("90000", "5", 2)
        es = A.post_sales_invoice(T, "S1", sale, cogs=D("120000"))
        ec = A.post_credit_note(T, "CN1", sale, cogs_reversal=D("120000"))
        pl = A.profit_and_loss(es + ec)
        self.assertEqual(pl["net_revenue"], D("0"))
        self.assertEqual(pl["net_profit_before_income_tax"], D("0"))
        self.assertEqual(A.account_balance(es + ec, A.AR), D("0"))


class PaymentTests(unittest.TestCase):
    def test_fifo_allocation_and_advance(self):
        opens = [OpenInvoice(2, date(2026, 10, 10), D("500")), OpenInvoice(1, date(2026, 9, 1), D("300"))]
        alloc, advance = allocate_fifo("900", opens)
        self.assertEqual(alloc, [(1, D("300")), (2, D("500"))])
        self.assertEqual(advance, D("100"))
        alloc, advance = allocate_fifo("350", opens)
        self.assertEqual(alloc, [(1, D("300")), (2, D("50"))])
        self.assertEqual(advance, D("0"))
        with self.assertRaises(PaymentError):
            allocate_fifo("0", opens)

    def test_explicit_allocation_validation(self):
        bal = {1: D("300"), 2: D("500")}
        self.assertEqual(validate_allocations("400", {1: "300"}, bal), D("100"))
        for bad in ({1: "301"}, {3: "1"}, {1: "300", 2: "200"}, {1: "0"}):
            with self.subTest(bad=bad), self.assertRaises(PaymentError):
                validate_allocations("400", bad, bal)

    def test_partial_then_full_payment_status(self):
        due = date(2026, 12, 1)
        self.assertEqual(invoice_status("189000", "0", due, T), "unpaid")
        self.assertEqual(invoice_status("189000", "100000", due, T), "partially_paid")
        self.assertEqual(invoice_status("189000", "189000", due, T), "paid")
        self.assertEqual(invoice_status("189000", "100000", date(2026, 11, 1), T), "overdue")
        self.assertEqual(invoice_status("189000", "0", due, T, refunded=True), "refunded")
        with self.assertRaises(PaymentError):
            invoice_status("100", "101", due, T)


if __name__ == "__main__":
    unittest.main()
