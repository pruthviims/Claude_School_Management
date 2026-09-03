"""
Collection: receiving money and allocating it against charges.

Two things here are easy to get wrong and expensive to fix:

  1. Allocation. Partial payment is normal, so a payment cannot simply point
     at an invoice. It splits across specific charges, oldest due first.

  2. Clearing status. A cheque or an unconfirmed gateway payment is NOT money
     until it clears. Only CLEARED payments count toward the balance, and a
     bounce reverses the allocations rather than deleting the payment.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import date

from django.db import IntegrityError, transaction

from fees.models import (
    Allocation,
    Charge,
    DocumentCounter,
    Enrollment,
    Payment,
)
from fees.services.billing import fiscal_year_for

log = logging.getLogger(__name__)


class CollectionError(Exception):
    pass


def _next_receipt_no(school_id, received_on: date) -> str:
    return DocumentCounter.issue(
        school_id=school_id,
        doc_type=DocumentCounter.DocType.RECEIPT,
        fiscal_year=fiscal_year_for(received_on),
        prefix="RCP/",
    )


def _allocate(payment: Payment, charge_amounts: list[tuple] | None = None) -> list:
    """
    Spread a payment across charges.

    Default policy is oldest-due-first with arrears ahead of current dues,
    which is what a school office does by hand. An explicit list overrides it
    for the cases where a parent insists on paying a specific head.
    """
    allocations = []

    if charge_amounts:
        pairs = charge_amounts
    else:
        charges = Charge.objects.select_for_update().filter(
            enrollment=payment.enrollment, reversed_by__isnull=True
        ).order_by("-is_arrear", "due_on", "id")

        remaining = payment.amount
        pairs = []
        for charge in charges:
            if remaining <= 0:
                break
            take = min(remaining, charge.outstanding)
            if take > 0:
                pairs.append((charge, take))
                remaining -= take

        if remaining > 0:
            # Advance payment. Recorded but unallocated — it shows up as
            # negative balance and settles against the next term's charges.
            log.info(
                "Payment %s has %d paise unallocated (advance).",
                payment.receipt_no, remaining,
            )

    for charge, amount in pairs:
        if amount > charge.outstanding:
            raise CollectionError(
                f"Cannot allocate {amount} to charge {charge.id}; "
                f"only {charge.outstanding} outstanding."
            )
        allocations.append(
            Allocation(
                school_id=payment.school_id,
                payment=payment,
                charge=charge,
                amount=amount,
            )
        )

    Allocation.objects.bulk_create(allocations)
    return allocations


@transaction.atomic
def record_payment(
    enrollment: Enrollment,
    *,
    amount: int,
    mode: str,
    received_on: date | None = None,
    instrument_ref: str = "",
    collected_by=None,
    charge_amounts: list[tuple] | None = None,
    gateway: str = "",
    gateway_order_id: str = "",
    gateway_payment_id: str = "",
    convenience_fee: int = 0,
) -> Payment:
    """
    Record money received and allocate it.

    Cash, UPI, card and net banking clear immediately. Cheques and DDs sit in
    PENDING until someone confirms the bank credit.
    """
    if amount <= 0:
        raise CollectionError("Payment amount must be positive.")
    if not enrollment.academic_year.is_editable:
        raise CollectionError("Cannot post payments to a closed academic year.")

    received_on = received_on or date.today()
    instant = mode not in (Payment.Mode.CHEQUE, Payment.Mode.DD)

    payment = Payment.objects.create(
        school_id=enrollment.school_id,
        receipt_no=_next_receipt_no(enrollment.school_id, received_on),
        enrollment=enrollment,
        amount=amount,
        mode=mode,
        clearing_status=(
            Payment.Clearing.CLEARED if instant else Payment.Clearing.PENDING
        ),
        received_on=received_on,
        cleared_on=received_on if instant else None,
        instrument_ref=instrument_ref,
        collected_by=collected_by,
        created_by=collected_by,
        gateway=gateway,
        gateway_order_id=gateway_order_id,
        gateway_payment_id=gateway_payment_id,
        convenience_fee=convenience_fee,
    )
    _allocate(payment, charge_amounts)
    return payment


@transaction.atomic
def mark_cleared(payment: Payment, *, cleared_on: date | None = None) -> Payment:
    if payment.clearing_status == Payment.Clearing.CLEARED:
        return payment
    payment.clearing_status = Payment.Clearing.CLEARED
    payment.cleared_on = cleared_on or date.today()
    payment.save(update_fields=["clearing_status", "cleared_on"])
    return payment


@transaction.atomic
def mark_bounced(payment: Payment, *, reason: str = "") -> Payment:
    """
    A bounced cheque does not delete the payment — the receipt was issued and
    the number must stay in the sequence. Flip the status and drop the
    allocations so the balance reopens.
    """
    payment.clearing_status = Payment.Clearing.BOUNCED
    payment.reversal_reason = reason or "Instrument bounced"
    payment.cleared_on = None
    payment.save(
        update_fields=["clearing_status", "reversal_reason", "cleared_on"]
    )
    payment.allocations.all().delete()
    log.warning("Payment %s bounced: %s", payment.receipt_no, reason)
    return payment


# ---------------------------------------------------------------------
# Online payments
# ---------------------------------------------------------------------

def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """
    Razorpay-style HMAC-SHA256 over the raw request body.

    Use the RAW bytes, not the parsed-and-re-serialised JSON — key ordering
    will differ and every signature will fail.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@transaction.atomic
def handle_gateway_webhook(
    *,
    enrollment: Enrollment,
    gateway: str,
    gateway_order_id: str,
    gateway_payment_id: str,
    amount: int,
    convenience_fee: int = 0,
) -> tuple[Payment, bool]:
    """
    Idempotent webhook handler. Returns (payment, created).

    Gateways retry, duplicate and reorder deliveries. The unique constraint on
    (gateway, gateway_payment_id) is the real defence — we catch the
    IntegrityError rather than checking-then-inserting, because the
    check-then-insert race is exactly what concurrent retries will hit.
    """
    existing = Payment.objects.filter(
        gateway=gateway, gateway_payment_id=gateway_payment_id
    ).first()
    if existing:
        log.info("Duplicate webhook for %s ignored.", gateway_payment_id)
        return existing, False

    try:
        with transaction.atomic():
            payment = record_payment(
                enrollment,
                amount=amount,
                mode=Payment.Mode.UPI,
                gateway=gateway,
                gateway_order_id=gateway_order_id,
                gateway_payment_id=gateway_payment_id,
                convenience_fee=convenience_fee,
                instrument_ref=gateway_payment_id,
            )
        return payment, True
    except IntegrityError:
        # Concurrent retry won the race; its row is authoritative.
        payment = Payment.objects.get(
            gateway=gateway, gateway_payment_id=gateway_payment_id
        )
        return payment, False


def daily_collection(school_id, on: date) -> dict:
    """Day book. The number the office reconciles against the cash drawer."""
    payments = Payment.objects.filter(
        school_id=school_id,
        received_on=on,
        clearing_status=Payment.Clearing.CLEARED,
        reversed_by__isnull=True,
    ).select_related("enrollment__student")

    by_mode: dict[str, int] = {}
    total = 0
    for p in payments:
        by_mode[p.mode] = by_mode.get(p.mode, 0) + p.amount
        total += p.amount
    return {"date": on, "total": total, "by_mode": by_mode, "count": len(payments)}
