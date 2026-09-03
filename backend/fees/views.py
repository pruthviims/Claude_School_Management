"""
REST API.

Note the webhook endpoint is the only unauthenticated route, and it is
protected by HMAC signature verification instead of a session. Everything
else requires an authenticated staff user.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from fees.models import (
    AcademicYear,
    Enrollment,
    Invoice,
    Payment,
    Section,
    Student,
    rupees_to_paise,
)
from fees.services import billing, collection, promotion, receipts


# ---------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------

class StudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = [
            "id", "admission_no", "full_name", "date_of_birth", "gender",
            "guardian_name", "guardian_phone", "guardian_email", "status",
        ]


class EnrollmentSerializer(serializers.ModelSerializer):
    student = StudentSerializer(read_only=True)
    section_label = serializers.CharField(source="section.__str__", read_only=True)
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Enrollment
        fields = [
            "id", "student", "academic_year", "class_level", "section",
            "section_label", "stream", "roll_no", "admission_type",
            "outcome", "is_active", "balance",
        ]

    def get_balance(self, obj):
        return obj.ledger()


class AdmissionSerializer(serializers.Serializer):
    admission_no = serializers.CharField(max_length=30)
    full_name = serializers.CharField(max_length=150)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.CharField(max_length=20, required=False, allow_blank=True)
    guardian_name = serializers.CharField(max_length=150)
    guardian_phone = serializers.CharField(max_length=20)
    guardian_email = serializers.EmailField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    section_id = serializers.UUIDField()
    stream_id = serializers.UUIDField(required=False, allow_null=True)
    optional_head_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    # DPDP: verifiable parental consent is captured at admission, not implied.
    consent_notice_id = serializers.UUIDField()
    consent_granted_by = serializers.CharField(max_length=150)
    consent_relationship = serializers.CharField(max_length=40)
    consent_method = serializers.CharField(max_length=20)
    consent_reference = serializers.CharField(
        max_length=200, required=False, allow_blank=True
    )


class PaymentSerializer(serializers.Serializer):
    enrollment_id = serializers.UUIDField()
    amount_rupees = serializers.DecimalField(max_digits=10, decimal_places=2)
    mode = serializers.ChoiceField(choices=Payment.Mode.choices)
    instrument_ref = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )


# ---------------------------------------------------------------------
# Admissions
# ---------------------------------------------------------------------

@api_view(["POST"])
def admit_student(request):
    """New admission: student, enrollment, consent record and charges."""
    from django.db import transaction

    from fees.models import ConsentNotice, ConsentRecord

    s = AdmissionSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    d = s.validated_data

    section = get_object_or_404(Section, id=d["section_id"], school=request.school)
    notice = get_object_or_404(
        ConsentNotice, id=d["consent_notice_id"], school=request.school
    )

    with transaction.atomic():
        student = Student.objects.create(
            school=request.school,
            admission_no=d["admission_no"],
            full_name=d["full_name"],
            date_of_birth=d.get("date_of_birth"),
            gender=d.get("gender", ""),
            guardian_name=d["guardian_name"],
            guardian_phone=d["guardian_phone"],
            guardian_email=d.get("guardian_email", ""),
            address=d.get("address", ""),
            created_by=request.user,
        )
        ConsentRecord.objects.create(
            school=request.school,
            student=student,
            notice=notice,
            purpose=ConsentRecord.Purpose.FEE_ADMIN,
            granted_by_name=d["consent_granted_by"],
            relationship=d["consent_relationship"],
            verification_method=d["consent_method"],
            verification_ref=d.get("consent_reference", ""),
            source_ip=request.META.get("REMOTE_ADDR"),
            created_by=request.user,
        )
        enrollment = Enrollment(
            school=request.school,
            student=student,
            academic_year=section.academic_year,
            class_level=section.class_level,
            section=section,
            stream_id=d.get("stream_id"),
            admission_type=Enrollment.AdmissionType.NEW,
            created_by=request.user,
        )
        enrollment.full_clean(exclude=["created_by"])
        enrollment.save()

        billing.generate_charges(
            enrollment,
            optional_head_ids=d.get("optional_head_ids", []),
            created_by=request.user,
        )

    return Response(
        EnrollmentSerializer(enrollment).data, status=status.HTTP_201_CREATED
    )


@api_view(["GET"])
def enrollment_ledger(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment, id=enrollment_id, school=request.school
    )
    ledger = enrollment.ledger()
    charges = [
        {
            "id": str(c.id),
            "head": c.head_name,
            "term": c.term_no,
            "due_on": c.due_on,
            "amount": c.amount,
            "settled": c.settled,
            "outstanding": c.outstanding,
            "is_arrear": c.is_arrear,
        }
        for c in enrollment.charges.filter(reversed_by__isnull=True)
    ]
    return Response({"summary": ledger, "charges": charges})


# ---------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------

@api_view(["POST"])
def collect_payment(request):
    s = PaymentSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    d = s.validated_data

    enrollment = get_object_or_404(
        Enrollment, id=d["enrollment_id"], school=request.school
    )
    try:
        payment = collection.record_payment(
            enrollment,
            amount=rupees_to_paise(d["amount_rupees"]),
            mode=d["mode"],
            instrument_ref=d.get("instrument_ref", ""),
            collected_by=request.user,
        )
    except collection.CollectionError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "receipt_no": payment.receipt_no,
            "amount": payment.amount,
            "clearing_status": payment.clearing_status,
            "balance": enrollment.ledger(),
            "pdf_url": f"/api/payments/{payment.id}/receipt.pdf",
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def receipt_pdf(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id, school=request.school)
    # Any reprint is watermarked DUPLICATE so the original stays identifiable.
    is_duplicate = request.query_params.get("reprint") == "1"
    pdf = receipts.receipt_pdf(payment, is_duplicate=is_duplicate)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="receipt-{payment.receipt_no.replace("/", "-")}.pdf"'
    )
    return response


@api_view(["POST"])
def issue_bill(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment, id=enrollment_id, school=request.school
    )
    try:
        invoice = billing.issue_invoice(
            enrollment,
            term_no=request.data.get("term_no"),
            created_by=request.user,
        )
    except billing.BillingError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        {
            "invoice_no": invoice.invoice_no,
            "total": invoice.total,
            "pdf_url": f"/api/invoices/{invoice.id}/bill.pdf",
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def bill_pdf(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, school=request.school)
    pdf = receipts.invoice_pdf(
        invoice, is_duplicate=request.query_params.get("reprint") == "1"
    )
    return HttpResponse(pdf, content_type="application/pdf")


# ---------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------

@api_view(["POST"])
def promotion_preview(request):
    from_year = get_object_or_404(
        AcademicYear, id=request.data["from_year_id"], school=request.school
    )
    to_year = get_object_or_404(
        AcademicYear, id=request.data["to_year_id"], school=request.school
    )
    try:
        pv = promotion.preview(
            from_year=from_year,
            to_year=to_year,
            block_on_dues=request.data.get("block_on_dues", False),
            exclude_enrollment_ids=set(request.data.get("exclude", [])),
        )
        promotion.assign_sections(
            pv.moves, to_year=to_year,
            strategy=request.data.get("section_strategy", "keep"),
        )
    except promotion.PromotionError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def render(move):
        return {
            "enrollment_id": str(move.enrollment.id),
            "student": move.enrollment.student.full_name,
            "admission_no": move.enrollment.student.admission_no,
            "from": str(move.enrollment.section),
            "to_class": move.to_class.name if move.to_class else None,
            "to_section": str(move.to_section) if move.to_section else None,
            "balance": move.balance,
            "needs_stream": move.needs_stream,
            "needs_optin": move.needs_optin,
            "blocked_reason": move.blocked_reason,
            "actionable": move.is_actionable,
        }

    return Response({
        "summary": pv.summary(),
        "moves": [render(m) for m in pv.moves],
        "graduating": [render(m) for m in pv.graduating],
        "blocked": [render(m) for m in pv.blocked],
    })


# ---------------------------------------------------------------------
# Gateway webhook — the only unauthenticated route
# ---------------------------------------------------------------------

@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def gateway_webhook(request):
    """
    The webhook is the source of truth for online payments, never the browser
    redirect. Signature is computed over the RAW body: re-serialising the
    parsed JSON changes key order and every signature fails.
    """
    signature = request.META.get("HTTP_X_RAZORPAY_SIGNATURE", "")
    secret = settings.FEES["GATEWAY_WEBHOOK_SECRET"]

    if not collection.verify_webhook_signature(request.body, signature, secret):
        return Response(
            {"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED
        )

    payload = json.loads(request.body)
    entity = payload["payload"]["payment"]["entity"]
    notes = entity.get("notes", {})

    enrollment = get_object_or_404(Enrollment, id=notes["enrollment_id"])
    payment, created = collection.handle_gateway_webhook(
        enrollment=enrollment,
        gateway=settings.FEES["GATEWAY"],
        gateway_order_id=entity.get("order_id", ""),
        gateway_payment_id=entity["id"],
        amount=entity["amount"],          # gateways already send paise
        convenience_fee=int(notes.get("convenience_fee", 0)),
    )
    # Always 200 on a valid signature, or the gateway retries forever.
    return Response({"receipt_no": payment.receipt_no, "created": created})


# ---------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------

@api_view(["GET"])
def day_book(request):
    from datetime import date as _date

    on = request.query_params.get("date")
    on = _date.fromisoformat(on) if on else _date.today()
    return Response(collection.daily_collection(request.school_id, on))


@api_view(["GET"])
def defaulters(request):
    year = get_object_or_404(
        AcademicYear, id=request.query_params["year_id"], school=request.school
    )
    summary = billing.outstanding_summary(year)
    return Response({
        "total": summary["total"],
        "count": summary["count"],
        "rows": [
            {
                "student": r["enrollment"].student.full_name,
                "admission_no": r["enrollment"].student.admission_no,
                "class": str(r["enrollment"].section),
                "balance": r["balance"],
            }
            for r in summary["rows"]
        ],
    })
