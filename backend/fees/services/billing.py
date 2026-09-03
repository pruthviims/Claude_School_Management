"""
Billing: turning the published fee structure into charges a student owes,
and turning charges into an invoice document.

The critical rule enforced here: charges are a SNAPSHOT. Once written, a
later edit to FeeStructure must not change what an existing student owes.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import transaction
from django.db.models import Q

from fees.models import (
    AcademicYear,
    Charge,
    DocumentCounter,
    Enrollment,
    FeeHead,
    FeeStructure,
    Invoice,
)

log = logging.getLogger(__name__)


def fiscal_year_for(d: date) -> str:
    """Indian financial year: 1 April to 31 March."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


class BillingError(Exception):
    pass


@transaction.atomic
def generate_charges(
    enrollment: Enrollment,
    *,
    optional_head_ids: list | None = None,
    created_by=None,
) -> list[Charge]:
    """
    Snapshot the fee structure onto an enrollment.

    Idempotent per (enrollment, fee_head, term_no): re-running will not
    duplicate charges, so a half-failed admission can safely be retried.

    Optional heads (transport, hostel) are only charged when explicitly
    opted into — passing them in optional_head_ids.
    """
    if not enrollment.academic_year.is_editable:
        raise BillingError(
            f"{enrollment.academic_year} is closed; no new charges may be posted."
        )

    optional_head_ids = set(optional_head_ids or [])

    structure_q = Q(
        school_id=enrollment.school_id,
        academic_year_id=enrollment.academic_year_id,
        class_level_id=enrollment.class_level_id,
    )
    # A stream-specific price wins over the generic one; take both and
    # de-duplicate below so Science students get lab fees and Arts don't.
    structure_q &= Q(stream__isnull=True) | Q(stream_id=enrollment.stream_id)

    lines = (
        FeeStructure.objects.filter(structure_q)
        .select_related("fee_head", "stream")
        .order_by("fee_head__display_order", "term_no", "stream_id")
    )

    # Existing charges guard idempotency.
    already = set(
        Charge.objects.filter(
            enrollment=enrollment, source=Charge.Source.STRUCTURE
        ).values_list("fee_head_id", "term_no")
    )

    seen: set[tuple] = set()
    created: list[Charge] = []

    for line in lines:
        key = (line.fee_head_id, line.term_no)

        # Stream-specific row already handled this head+term.
        if key in seen:
            continue

        head = line.fee_head
        if head.is_optional and head.id not in optional_head_ids:
            continue
        # One-time heads (admission fee) apply only to genuinely new students.
        if head.is_one_time and enrollment.admission_type != Enrollment.AdmissionType.NEW:
            continue

        seen.add(key)
        if key in already:
            continue

        created.append(
            Charge(
                school_id=enrollment.school_id,
                enrollment=enrollment,
                fee_head=head,
                head_name=head.name,          # frozen label
                amount=line.amount,           # frozen amount
                term_no=line.term_no,
                due_on=line.due_on,
                source=Charge.Source.STRUCTURE,
                created_by=created_by,
            )
        )

    Charge.objects.bulk_create(created)
    log.info(
        "Generated %d charges for enrollment %s", len(created), enrollment.id
    )
    return created


@transaction.atomic
def carry_forward_arrears(
    *,
    from_enrollment: Enrollment,
    to_enrollment: Enrollment,
    created_by=None,
) -> Charge | None:
    """
    Move an unpaid balance into the new academic year as a single opening
    arrear charge, flagged so it reports separately from current-year dues.

    Called by the promotion engine. Idempotent: re-running finds the existing
    arrear row and does nothing.
    """
    outstanding = from_enrollment.balance
    if outstanding <= 0:
        return None

    existing = Charge.objects.filter(
        enrollment=to_enrollment,
        is_arrear=True,
        source_year=from_enrollment.academic_year,
    ).first()
    if existing:
        return existing

    return Charge.objects.create(
        school_id=to_enrollment.school_id,
        enrollment=to_enrollment,
        fee_head=None,
        head_name=f"Arrears carried forward ({from_enrollment.academic_year.name})",
        amount=outstanding,
        term_no=1,
        due_on=to_enrollment.academic_year.starts_on,
        source=Charge.Source.ARREAR,
        is_arrear=True,
        source_year=from_enrollment.academic_year,
        created_by=created_by,
    )


@transaction.atomic
def issue_invoice(
    enrollment: Enrollment,
    *,
    term_no: int | None = None,
    include_arrears: bool = True,
    issued_on: date | None = None,
    created_by=None,
) -> Invoice:
    """
    Bundle unbilled charges into a numbered demand note.

    A charge belongs to at most one invoice, so re-issuing for the same term
    picks up only what has not already been billed.
    """
    issued_on = issued_on or date.today()

    charges = Charge.objects.select_for_update().filter(
        enrollment=enrollment, invoice__isnull=True, reversed_by__isnull=True
    )
    if term_no is not None:
        term_filter = Q(term_no=term_no)
        if include_arrears:
            term_filter |= Q(is_arrear=True)
        charges = charges.filter(term_filter)
    elif not include_arrears:
        charges = charges.filter(is_arrear=False)

    charges = list(charges)
    if not charges:
        raise BillingError("Nothing outstanding to invoice for this enrollment.")

    invoice_no = DocumentCounter.issue(
        school_id=enrollment.school_id,
        doc_type=DocumentCounter.DocType.INVOICE,
        fiscal_year=fiscal_year_for(issued_on),
        prefix="INV/",
    )

    invoice = Invoice.objects.create(
        school_id=enrollment.school_id,
        invoice_no=invoice_no,
        enrollment=enrollment,
        issued_on=issued_on,
        due_on=min(c.due_on for c in charges),
        # Frozen so the invoice remains reprintable after DPDP de-identification.
        student_name_at_issue=enrollment.student.full_name,
        class_at_issue=str(enrollment.section),
        created_by=created_by,
    )
    Charge.objects.filter(id__in=[c.id for c in charges]).update(invoice=invoice)
    return invoice


def outstanding_summary(academic_year: AcademicYear) -> dict:
    """Defaulter roll-up used by the dues report."""
    rows = []
    total = 0
    qs = (
        Enrollment.objects.filter(academic_year=academic_year, is_active=True)
        .select_related("student", "section", "section__class_level")
    )
    for enrollment in qs:
        balance = enrollment.balance
        if balance > 0:
            total += balance
            rows.append({"enrollment": enrollment, "balance": balance})
    rows.sort(key=lambda r: -r["balance"])
    return {"rows": rows, "total": total, "count": len(rows)}
