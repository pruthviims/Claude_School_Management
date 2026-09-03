"""
These tests exist to pin the invariants that are expensive to discover in
production: derived balances, gapless receipt numbers, cheque bounces
reopening dues, webhook idempotency, and arrears surviving promotion.
"""

from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase

from fees.models import (
    AcademicYear,
    ClassLevel,
    DocumentCounter,
    Enrollment,
    FeeHead,
    Payment,
    School,
    Section,
    Student,
    Stream,
    rupees_to_paise,
)
from fees.services import billing, collection, promotion
from fees.services.receipts import amount_in_words, format_inr


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_school", "--code", "test", "--year", "2026-27",
                     verbosity=0)
        cls.school = School.objects.get(short_code="test")
        cls.year = AcademicYear.objects.get(school=cls.school, name="2026-27")
        cls.user = User.objects.create_user("clerk", password="x" * 14)

    def admit(self, name, admission_no, class_name="VIII", section_name="A",
              stream=None, admission_type=Enrollment.AdmissionType.NEW):
        level = ClassLevel.objects.get(school=self.school, name=class_name)
        section = Section.objects.get(
            school=self.school, academic_year=self.year,
            class_level=level, name=section_name,
        )
        student = Student.objects.create(
            school=self.school, admission_no=admission_no, full_name=name,
            guardian_name="Guardian", guardian_phone="9000000000",
        )
        enrollment = Enrollment.objects.create(
            school=self.school, student=student, academic_year=self.year,
            class_level=level, section=section, stream=stream,
            admission_type=admission_type,
        )
        billing.generate_charges(enrollment)
        return enrollment


class LedgerTests(Base):

    def test_balance_is_derived_from_charges_and_payments(self):
        e = self.admit("Asha Rao", "A001")
        charged = e.ledger()["charged"]
        self.assertGreater(charged, 0)
        self.assertEqual(e.balance, charged)

        collection.record_payment(
            e, amount=rupees_to_paise(10000), mode=Payment.Mode.CASH,
            collected_by=self.user,
        )
        self.assertEqual(e.balance, charged - rupees_to_paise(10000))

    def test_partial_payment_allocates_oldest_due_first(self):
        e = self.admit("Bhavya S", "A002")
        first = e.charges.order_by("due_on", "id").first()

        collection.record_payment(
            e, amount=first.amount, mode=Payment.Mode.UPI,
            collected_by=self.user,
        )
        first.refresh_from_db()
        self.assertEqual(first.outstanding, 0)

    def test_charges_are_idempotent(self):
        e = self.admit("Chandan K", "A003")
        before = e.charges.count()
        billing.generate_charges(e)
        self.assertEqual(e.charges.count(), before)

    def test_editing_fee_structure_does_not_change_issued_charges(self):
        """The snapshot rule — the whole reason charges duplicate the amount."""
        e = self.admit("Divya N", "A004")
        charge = e.charges.filter(head_name="Tuition fee").first()
        original = charge.amount

        from fees.models import FeeStructure
        FeeStructure.objects.filter(
            academic_year=self.year, class_level=e.class_level,
            fee_head__name="Tuition fee",
        ).update(amount=rupees_to_paise(999999))

        charge.refresh_from_db()
        self.assertEqual(charge.amount, original)

    def test_cheque_is_not_money_until_it_clears(self):
        e = self.admit("Esha P", "A005")
        before = e.balance

        payment = collection.record_payment(
            e, amount=rupees_to_paise(5000), mode=Payment.Mode.CHEQUE,
            instrument_ref="123456", collected_by=self.user,
        )
        self.assertEqual(payment.clearing_status, Payment.Clearing.PENDING)
        self.assertEqual(e.balance, before)          # unchanged

        collection.mark_cleared(payment)
        self.assertEqual(e.balance, before - rupees_to_paise(5000))

    def test_bounced_cheque_reopens_the_due_but_keeps_the_receipt(self):
        e = self.admit("Farhan A", "A006")
        before = e.balance
        payment = collection.record_payment(
            e, amount=rupees_to_paise(5000), mode=Payment.Mode.CHEQUE,
            collected_by=self.user,
        )
        collection.mark_cleared(payment)
        collection.mark_bounced(payment, reason="Insufficient funds")

        self.assertEqual(e.balance, before)
        self.assertTrue(
            Payment.objects.filter(receipt_no=payment.receipt_no).exists(),
            "Receipt number must survive a bounce — it was issued.",
        )


class NumberingTests(Base):

    def test_receipt_numbers_are_sequential_and_gapless(self):
        e = self.admit("Gita M", "A007")
        numbers = [
            collection.record_payment(
                e, amount=rupees_to_paise(1000), mode=Payment.Mode.CASH,
                collected_by=self.user,
            ).receipt_no
            for _ in range(5)
        ]
        tail = [int(n.split("/")[-1]) for n in numbers]
        self.assertEqual(tail, list(range(1, 6)))

    def test_counter_refuses_to_run_outside_a_transaction(self):
        """
        A rollback outside an atomic block would silently burn a number and
        leave a gap in the receipt sequence, which is exactly what an auditor
        will query. Django's TestCase runs inside its own atomic block, so the
        no-transaction condition is simulated rather than entered.
        """
        from unittest import mock

        conn = mock.MagicMock()
        conn.in_atomic_block = False
        with mock.patch("fees.models.transaction.get_connection", return_value=conn):
            with self.assertRaises(RuntimeError):
                DocumentCounter.issue(
                    school_id=self.school.id,
                    doc_type=DocumentCounter.DocType.RECEIPT,
                    fiscal_year="2026-27",
                )

    def test_fiscal_year_follows_april_to_march(self):
        self.assertEqual(billing.fiscal_year_for(date(2026, 3, 31)), "2025-26")
        self.assertEqual(billing.fiscal_year_for(date(2026, 4, 1)), "2026-27")


class GatewayTests(Base):

    def test_duplicate_webhook_does_not_double_credit(self):
        e = self.admit("Hari V", "A008")
        before = e.balance
        args = dict(
            enrollment=e, gateway="razorpay", gateway_order_id="order_1",
            gateway_payment_id="pay_ABC123", amount=rupees_to_paise(5000),
        )
        p1, created1 = collection.handle_gateway_webhook(**args)
        p2, created2 = collection.handle_gateway_webhook(**args)

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(p1.id, p2.id)
        self.assertEqual(e.balance, before - rupees_to_paise(5000))

    def test_signature_verification_rejects_tampering(self):
        secret = "whsec_test"
        body = b'{"event":"payment.captured"}'
        import hashlib
        import hmac
        good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        self.assertTrue(collection.verify_webhook_signature(body, good, secret))
        self.assertFalse(
            collection.verify_webhook_signature(b'{"event":"tampered"}', good, secret)
        )
        self.assertFalse(collection.verify_webhook_signature(body, "", secret))


class PromotionTests(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.next_year = AcademicYear.objects.create(
            school=cls.school, name="2027-28",
            starts_on=date(2027, 6, 1), ends_on=date(2028, 3, 31),
            status=AcademicYear.Status.PLANNING,
        )
        for level in ClassLevel.objects.filter(school=cls.school):
            for name in ("A", "B", "C"):
                Section.objects.create(
                    school=cls.school, academic_year=cls.next_year,
                    class_level=level, name=name, capacity=40,
                )

    def test_promotion_moves_student_up_one_rung(self):
        e = self.admit("Ishan T", "B001", class_name="VIII")
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)
        promotion.assign_sections(pv.moves, to_year=self.next_year)
        promotion.commit(
            from_year=self.year, to_year=self.next_year,
            moves=pv.moves, committed_by=self.user,
        )
        new = Enrollment.objects.get(student=e.student, academic_year=self.next_year)
        self.assertEqual(new.class_level.name, "IX")
        self.assertEqual(new.admission_type, Enrollment.AdmissionType.CARRY_OVER)

    def test_arrears_carry_forward_as_a_flagged_charge(self):
        e = self.admit("Jaya L", "B002", class_name="VIII")
        due = e.balance
        self.assertGreater(due, 0)

        pv = promotion.preview(from_year=self.year, to_year=self.next_year)
        promotion.assign_sections(pv.moves, to_year=self.next_year)
        promotion.commit(
            from_year=self.year, to_year=self.next_year,
            moves=pv.moves, committed_by=self.user,
        )
        new = Enrollment.objects.get(student=e.student, academic_year=self.next_year)
        arrear = new.charges.filter(is_arrear=True).first()

        self.assertIsNotNone(arrear, "Unpaid dues must follow the student.")
        self.assertEqual(arrear.amount, due)
        self.assertEqual(arrear.source_year, self.year)

    def test_class_x_to_puc_requires_explicit_optin(self):
        """The branch that naive promotion engines get wrong."""
        self.admit("Kiran D", "B003", class_name="X")
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)
        move = next(m for m in pv.moves if m.enrollment.student.admission_no == "B003")

        self.assertTrue(move.needs_optin)
        self.assertFalse(move.is_actionable)

    def test_puc_stream_carries_forward_automatically(self):
        stream = Stream.objects.get(school=self.school, combination="PCMB")
        self.admit("Latha R", "B004", class_name="1st PU", stream=stream)
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)
        move = next(m for m in pv.moves if m.enrollment.student.admission_no == "B004")

        self.assertEqual(move.to_class.name, "2nd PU")
        self.assertFalse(move.needs_stream)
        self.assertEqual(move.stream_id, stream.id)

    def test_second_puc_graduates_to_alumni(self):
        stream = Stream.objects.get(school=self.school, combination="SEBA")
        e = self.admit("Manoj B", "B005", class_name="2nd PU", stream=stream)
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)

        self.assertTrue(
            any(m.enrollment.id == e.id for m in pv.graduating),
            "2nd PUC is terminal — the student must not be promoted.",
        )

    def test_detained_student_is_not_promoted(self):
        e = self.admit("Nisha G", "B006", class_name="VII")
        e.outcome = Enrollment.Outcome.DETAINED
        e.save()
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)

        self.assertTrue(any(m.enrollment.id == e.id for m in pv.blocked))
        self.assertFalse(any(m.enrollment.id == e.id for m in pv.moves))

    def test_block_on_dues_policy_holds_defaulters_back(self):
        self.admit("Omkar J", "B007", class_name="VI")
        pv = promotion.preview(
            from_year=self.year, to_year=self.next_year, block_on_dues=True
        )
        blocked = [m.enrollment.student.admission_no for m in pv.blocked]
        self.assertIn("B007", blocked)

    def test_reverse_undoes_the_batch(self):
        e = self.admit("Priya H", "B008", class_name="V")
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)
        promotion.assign_sections(pv.moves, to_year=self.next_year)
        batch = promotion.commit(
            from_year=self.year, to_year=self.next_year,
            moves=pv.moves, committed_by=self.user,
        )
        self.assertTrue(
            Enrollment.objects.filter(
                student=e.student, academic_year=self.next_year
            ).exists()
        )

        promotion.reverse(batch)
        self.assertFalse(
            Enrollment.objects.filter(
                student=e.student, academic_year=self.next_year
            ).exists()
        )
        e.refresh_from_db()
        self.assertTrue(e.is_active)

    def test_sections_balance_when_target_is_smaller(self):
        for i in range(9):
            self.admit(f"Student {i}", f"C{i:03d}", class_name="III",
                       section_name="A")
        pv = promotion.preview(from_year=self.year, to_year=self.next_year)
        promotion.assign_sections(pv.moves, to_year=self.next_year,
                                  strategy="balance")
        targets = [
            m.to_section.name for m in pv.moves
            if m.to_class and m.to_class.name == "IV"
        ]
        spread = {n: targets.count(n) for n in set(targets)}
        self.assertGreater(len(spread), 1, "Balance strategy must spread students.")
        self.assertLessEqual(max(spread.values()) - min(spread.values()), 1)


class FormattingTests(TestCase):
    """Indian numbering. An accountant will reject 'one million'."""

    def test_amount_in_words_uses_lakh_and_crore(self):
        self.assertEqual(
            amount_in_words(rupees_to_paise(1234567)),
            "Twelve lakh thirty-four thousand five hundred sixty-seven rupees only",
        )
        self.assertEqual(
            amount_in_words(rupees_to_paise(45000)),
            "Forty-five thousand rupees only",
        )
        self.assertEqual(
            amount_in_words(rupees_to_paise(10000000)), "One crore rupees only"
        )

    def test_amount_in_words_handles_paise(self):
        self.assertEqual(
            amount_in_words(150075), "One thousand five hundred rupees and seventy-five paise only"
        )

    def test_indian_digit_grouping(self):
        self.assertEqual(format_inr(rupees_to_paise(1234567)), "12,34,567.00")
        self.assertEqual(format_inr(rupees_to_paise(45000)), "45,000.00")
        self.assertEqual(format_inr(rupees_to_paise(999)), "999.00")

    def test_money_never_uses_float(self):
        self.assertEqual(rupees_to_paise("0.1") + rupees_to_paise("0.2"),
                         rupees_to_paise("0.3"))
