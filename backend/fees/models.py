"""
Core domain models for the school fee management system.

Target: Django 4.2 LTS / 5.0.  On Django 5.1+ rename every
CheckConstraint(check=...) to CheckConstraint(condition=...) to silence
the deprecation warning; behaviour is identical.

=====================================================================
DESIGN INVARIANTS — do not violate these without a migration plan
=====================================================================

1.  MONEY IS INTEGER PAISE (BigIntegerField).  Never float. Never
    "rupees as Decimal" — mixing units is how you get off-by-a-paisa
    disputes in a fee register.

2.  CLASS AND SECTION LIVE ON Enrollment, NEVER ON Student.  A Student
    row is permanent identity; an Enrollment row is one student in one
    academic year. This is what makes history, reprints and
    year-over-year reporting possible.

3.  THE LEDGER IS APPEND-ONLY.  Charge, Concession, Payment and
    Allocation rows are never mutated or deleted after posting.
    Corrections are new reversing rows pointing at the original.

4.  EVERY TABLE CARRIES school_id.  One school today, Postgres RLS
    tomorrow. Adding tenancy later is the most expensive migration in
    this product category.

5.  BALANCE IS DERIVED, never stored as a mutable column.
        balance = charges - concessions - cleared allocations

6.  PERSONAL DATA AND FINANCIAL DATA ARE SEPARABLE.  DPDP erasure and
    the Income Tax Act pull in opposite directions. Student.deidentify()
    strips the personal layer while leaving a working ledger behind.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


# ---------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------

def rupees_to_paise(amount) -> int:
    """Convert a rupee amount to integer paise. Use at the API boundary only."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))


def paise_to_rupees(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


class MoneyField(models.BigIntegerField):
    """Integer paise. Named so nobody accidentally stores rupees in it."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("help_text", "Amount in paise (integer).")
        super().__init__(*args, **kwargs)


# ---------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------

class TimeStamped(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        editable=False,
    )

    class Meta:
        abstract = True


class TenantScopedManager(models.Manager):
    """
    All reads should go through .for_school(). When RLS is switched on the
    policy becomes the real enforcement and this stays as defence in depth.
    """

    def for_school(self, school_id):
        return self.get_queryset().filter(school_id=school_id)


class TenantScoped(TimeStamped):
    school = models.ForeignKey(
        "School",
        on_delete=models.CASCADE,  # deliberate: tenant offboarding must purge
        related_name="+",
    )

    objects = TenantScopedManager()

    class Meta:
        abstract = True


# ---------------------------------------------------------------------
# Tenant + academic structure
# ---------------------------------------------------------------------

class School(TimeStamped):
    name = models.CharField(max_length=200)
    short_code = models.SlugField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    logo_key = models.CharField(max_length=500, blank=True)
    # Printed on every receipt; schools change this and expect no deploy.
    receipt_footer = models.TextField(
        blank=True,
        default="Education services are exempt from GST. "
                "This is a computer generated receipt.",
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AcademicYear(TenantScoped):
    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"   # promotion batches reversible here
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"         # read-only forever

    name = models.CharField(max_length=20)          # "2026-27"
    starts_on = models.DateField()
    ends_on = models.DateField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PLANNING
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"], name="uniq_year_per_school"
            ),
            models.CheckConstraint(
                check=Q(ends_on__gt=models.F("starts_on")),
                name="year_ends_after_start",
            ),
        ]
        ordering = ["-starts_on"]

    def __str__(self):
        return self.name

    @property
    def is_editable(self) -> bool:
        return self.status != self.Status.CLOSED


class ClassLevel(TenantScoped):
    """
    The promotion ladder. ladder_order is the whole mechanism:
    LKG=1, UKG=2, I=3 ... X=12, 1st PUC=13, 2nd PUC=14.
    Promotion = find the level with ladder_order + 1.
    """

    class Stage(models.TextChoices):
        PRE_PRIMARY = "pre_primary", "Pre-primary"
        PRIMARY = "primary", "Primary"
        MIDDLE = "middle", "Middle"
        SECONDARY = "secondary", "Secondary"
        PUC = "puc", "Pre-university"

    name = models.CharField(max_length=30)                # "VIII", "1st PUC"
    ladder_order = models.PositiveSmallIntegerField()
    stage = models.CharField(max_length=15, choices=Stage.choices)

    # X -> 1st PUC is NOT an automatic promotion: many students leave for a
    # different board or college. Force an explicit per-student decision.
    requires_explicit_optin = models.BooleanField(
        default=False,
        help_text="Promotion into this level must be chosen per student.",
    )
    requires_stream = models.BooleanField(default=False)
    is_terminal = models.BooleanField(
        default=False, help_text="Students exit to alumni after this level."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "ladder_order"], name="uniq_ladder_order_per_school"
            ),
            models.UniqueConstraint(
                fields=["school", "name"], name="uniq_class_name_per_school"
            ),
        ]
        ordering = ["ladder_order"]

    def __str__(self):
        return self.name

    def next_level(self):
        if self.is_terminal:
            return None
        return ClassLevel.objects.filter(
            school_id=self.school_id, ladder_order=self.ladder_order + 1
        ).first()


class Section(TenantScoped):
    """
    Sections are year-scoped: a school may run three sections of VIII in one
    year and two the next. Capacity drives the auto-balance in promotion.
    """
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="sections"
    )
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.PROTECT, related_name="sections"
    )
    name = models.CharField(max_length=10)                # "A", "B"
    capacity = models.PositiveSmallIntegerField(default=40)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "academic_year", "class_level", "name"],
                name="uniq_section_per_class_year",
            )
        ]
        ordering = ["class_level__ladder_order", "name"]

    def __str__(self):
        return f"{self.class_level.name}-{self.name}"


class Stream(TenantScoped):
    """PUC streams and combinations: Science/PCMB, Commerce/SEBA, Arts/HEPS."""
    name = models.CharField(max_length=40)                # "Science"
    combination = models.CharField(max_length=40, blank=True)   # "PCMB"
    applies_to_stage = models.CharField(
        max_length=15, choices=ClassLevel.Stage.choices, default=ClassLevel.Stage.PUC
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name", "combination"], name="uniq_stream_per_school"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.combination})" if self.combination else self.name


# ---------------------------------------------------------------------
# Fee configuration
# ---------------------------------------------------------------------

class FeeHead(TenantScoped):
    class Basis(models.TextChoices):
        PER_CLASS = "per_class", "Priced per class"
        PER_SLAB = "per_slab", "Priced per slab (transport routes etc.)"
        FLAT = "flat", "Same for everyone"

    name = models.CharField(max_length=80)                # "Tuition fee"
    basis = models.CharField(
        max_length=12, choices=Basis.choices, default=Basis.PER_CLASS
    )
    is_one_time = models.BooleanField(
        default=False, help_text="Charged once at admission, e.g. admission fee."
    )
    is_optional = models.BooleanField(
        default=False, help_text="Opt-in per student, e.g. transport, hostel."
    )
    is_refundable = models.BooleanField(default=False)   # caution deposit
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"], name="uniq_fee_head_per_school"
            )
        ]
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class FeeStructure(TenantScoped):
    """
    The published price list for one year. Charges are SNAPSHOT from this at
    enrollment — editing a FeeStructure row must never retroactively alter a
    bill that has already been issued.
    """
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="fee_structures"
    )
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.PROTECT, related_name="fee_structures"
    )
    fee_head = models.ForeignKey(
        FeeHead, on_delete=models.PROTECT, related_name="fee_structures"
    )
    stream = models.ForeignKey(
        Stream, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    amount = MoneyField(validators=[MinValueValidator(0)])
    term_no = models.PositiveSmallIntegerField(
        default=1, help_text="1, 2, 3 for termwise instalments."
    )
    due_on = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "academic_year", "class_level",
                        "fee_head", "stream", "term_no"],
                name="uniq_fee_structure_line",
            ),
            models.CheckConstraint(
                check=Q(amount__gte=0), name="fee_structure_amount_non_negative"
            ),
        ]

    def __str__(self):
        return f"{self.class_level} {self.fee_head} T{self.term_no}"


# ---------------------------------------------------------------------
# People
# ---------------------------------------------------------------------

class BusRoute(TenantScoped):
    """
    Transport is priced by route, never by class — a class VIII child on the
    long Kanakapura route pays more than a 2nd PU child two stops away.
    That is why FeeHead.basis has a PER_SLAB option and why this sits outside
    FeeStructure entirely.
    """
    code = models.CharField(max_length=20)              # "R-04"
    name = models.CharField(max_length=120)             # "Kanakapura Road"
    distance_km = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True
    )
    vehicle_no = models.CharField(max_length=20, blank=True)
    driver_name = models.CharField(max_length=100, blank=True)
    driver_phone = models.CharField(max_length=20, blank=True)
    seats = models.PositiveSmallIntegerField(default=40)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_route_code_per_school"
            )
        ]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def seats_taken(self, academic_year):
        return self.riders.filter(
            enrollment__academic_year=academic_year, ended_on__isnull=True
        ).count()


class RouteStop(TenantScoped):
    """A pickup point. Fares are usually charged per stop, not per route."""
    route = models.ForeignKey(
        BusRoute, on_delete=models.CASCADE, related_name="stops"
    )
    name = models.CharField(max_length=120)
    sequence = models.PositiveSmallIntegerField(default=1)
    pickup_time = models.TimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["route", "name"], name="uniq_stop_name_per_route"
            )
        ]
        ordering = ["route__code", "sequence"]

    def __str__(self):
        return self.name


class TransportFare(TenantScoped):
    """
    Annual transport fare for one stop in one year, split across terms the
    same way tuition is. Snapshotted onto a Charge at enrollment, exactly
    like FeeStructure.
    """
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="transport_fares"
    )
    stop = models.ForeignKey(
        RouteStop, on_delete=models.PROTECT, related_name="fares"
    )
    amount = MoneyField(validators=[MinValueValidator(0)])
    term_no = models.PositiveSmallIntegerField(default=1)
    due_on = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "academic_year", "stop", "term_no"],
                name="uniq_transport_fare_line",
            )
        ]


class Student(TenantScoped):
    """
    Permanent identity only. Nothing year-specific belongs here.

    Every row is a child under the DPDP Act. The personal layer below is
    erasable; the ledger that references this row is not.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ALUMNI = "alumni", "Alumni"
        TRANSFERRED = "transferred", "Transferred out"
        LEFT = "left", "Left"

    admission_no = models.CharField(max_length=30)
    full_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    admitted_on = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.ACTIVE
    )

    # ---- Personal layer: erasable under DPDP once purpose is served -------
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_phone = models.CharField(max_length=20, blank=True)
    guardian_email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    photo_key = models.CharField(max_length=500, blank=True)
    # ----------------------------------------------------------------------

    deidentified_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "admission_no"], name="uniq_admission_no_per_school"
            )
        ]
        indexes = [models.Index(fields=["school", "full_name"])]

    def __str__(self):
        return f"{self.admission_no} {self.full_name}"

    @transaction.atomic
    def deidentify(self):
        """
        DPDP erasure without destroying the statutory financial record.

        Strips the personal layer and leaves the ledger intact. Invoices keep
        their frozen name_at_issue because reprinting a receipt for a past
        assessment year is a legal obligation, not a discretionary use.
        """
        self.full_name = f"[erased-{str(self.id)[:8]}]"
        self.date_of_birth = None
        self.gender = ""
        self.guardian_name = ""
        self.guardian_phone = ""
        self.guardian_email = ""
        self.address = ""
        self.photo_key = ""
        self.deidentified_at = timezone.now()
        self.save()
        ConsentRecord.objects.filter(
            student=self, withdrawn_at__isnull=True
        ).update(withdrawn_at=timezone.now())


class Enrollment(TenantScoped):
    """One student, one academic year. The unit everything financial hangs off."""

    class AdmissionType(models.TextChoices):
        NEW = "new", "New admission"
        CARRY_OVER = "carry_over", "Promoted from previous year"
        REPEAT = "repeat", "Repeating the class"
        READMISSION = "readmission", "Re-admission"

    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        PROMOTED = "promoted", "Promoted"
        DETAINED = "detained", "Detained"
        TC_ISSUED = "tc_issued", "TC issued"
        PASSED_OUT = "passed_out", "Passed out"
        LEFT = "left", "Left mid-year"

    student = models.ForeignKey(
        Student, on_delete=models.PROTECT, related_name="enrollments"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="enrollments"
    )
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.PROTECT, related_name="enrollments"
    )
    section = models.ForeignKey(
        Section, on_delete=models.PROTECT, related_name="enrollments"
    )
    stream = models.ForeignKey(
        Stream, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    roll_no = models.PositiveSmallIntegerField(null=True, blank=True)
    admission_type = models.CharField(max_length=15, choices=AdmissionType.choices)
    outcome = models.CharField(
        max_length=12, choices=Outcome.choices, default=Outcome.PENDING
    )
    promotion_batch = models.ForeignKey(
        "PromotionBatch", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="enrollments",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "student", "academic_year"],
                name="uniq_enrollment_per_student_year",
            ),
            models.UniqueConstraint(
                fields=["school", "section", "roll_no"],
                condition=Q(roll_no__isnull=False),
                name="uniq_roll_no_per_section",
            ),
        ]
        indexes = [models.Index(fields=["school", "academic_year", "class_level"])]

    def __str__(self):
        return f"{self.student.admission_no} — {self.section} ({self.academic_year})"

    def clean(self):
        if self.class_level.requires_stream and self.stream_id is None:
            raise ValidationError(
                {"stream": f"{self.class_level} requires a stream selection."}
            )
        if self.section.class_level_id != self.class_level_id:
            raise ValidationError({"section": "Section does not belong to this class."})
        if self.section.academic_year_id != self.academic_year_id:
            raise ValidationError({"section": "Section belongs to a different year."})

    # ---- Derived balance. Never cache this in a column. -------------------

    def ledger(self) -> dict:
        charged = self.charges.filter(reversed_by__isnull=True).aggregate(
            total=Sum("amount")
        )["total"] or 0
        conceded = self.concessions.filter(reversed_by__isnull=True).aggregate(
            total=Sum("amount")
        )["total"] or 0
        paid = Allocation.objects.filter(
            charge__enrollment=self,
            payment__clearing_status=Payment.Clearing.CLEARED,
            payment__reversed_by__isnull=True,
        ).aggregate(total=Sum("amount"))["total"] or 0

        return {
            "charged": charged,
            "conceded": conceded,
            "paid": paid,
            "balance": charged - conceded - paid,
        }

    @property
    def balance(self) -> int:
        return self.ledger()["balance"]


# ---------------------------------------------------------------------
# Ledger — append only
# ---------------------------------------------------------------------

class TransportAssignment(TenantScoped):
    """Which bus a student takes this year. Ended, never deleted."""
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="transport"
    )
    stop = models.ForeignKey(
        RouteStop, on_delete=models.PROTECT, related_name="riders"
    )
    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["school", "stop", "ended_on"])]


class ImportBatch(TenantScoped):
    """
    Staged bulk import. A school's first upload is always messy, so rows are
    validated and held before anything is written. Nothing touches Student
    until the admin confirms a clean dry run.
    """

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        VALIDATED = "validated", "Validated"
        COMMITTED = "committed", "Committed"
        CANCELLED = "cancelled", "Cancelled"

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="imports"
    )
    filename = models.CharField(max_length=255)
    column_map = models.JSONField(default=dict)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.UPLOADED
    )
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    committed_at = models.DateTimeField(null=True, blank=True)


class ImportRow(TenantScoped):
    """One line of the uploaded file, with whatever is wrong with it."""
    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="rows"
    )
    line_no = models.PositiveIntegerField()
    raw = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    student = models.ForeignKey(
        "Student", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["line_no"]

    @property
    def is_valid(self) -> bool:
        return not self.errors


class LedgerEntry(TenantScoped):
    """
    Base for anything that moves money. Reversal instead of mutation:
    a wrong row gets a counter-row and both stay visible to an auditor.
    """
    reversed_by = models.OneToOneField(
        "self", null=True, blank=True,
        on_delete=models.PROTECT, related_name="reverses",
    )
    reversal_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True

    @property
    def is_live(self) -> bool:
        return self.reversed_by_id is None


class Charge(LedgerEntry):
    """
    A snapshot of one fee line owed by one enrollment. Once written it is
    immutable — this is what protects already-issued bills from a mid-year
    revision of the fee structure.
    """

    class Source(models.TextChoices):
        STRUCTURE = "structure", "From fee structure"
        ARREAR = "arrear", "Carried forward arrear"
        MANUAL = "manual", "Manually added"

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="charges"
    )
    fee_head = models.ForeignKey(
        FeeHead, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Frozen label: the head may be renamed later, the historical bill must not change.
    head_name = models.CharField(max_length=80)
    amount = MoneyField(validators=[MinValueValidator(0)])
    term_no = models.PositiveSmallIntegerField(default=1)
    due_on = models.DateField()
    source = models.CharField(
        max_length=12, choices=Source.choices, default=Source.STRUCTURE
    )
    is_arrear = models.BooleanField(
        default=False, help_text="Carried forward from a previous year."
    )
    source_year = models.ForeignKey(
        AcademicYear, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
        help_text="For arrears: the year the dues originally arose in.",
    )
    invoice = models.ForeignKey(
        "Invoice", null=True, blank=True,
        on_delete=models.PROTECT, related_name="lines",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gte=0), name="charge_amount_non_negative"
            ),
            models.CheckConstraint(
                check=Q(is_arrear=False) | Q(source_year__isnull=False),
                name="arrear_requires_source_year",
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment", "due_on"]),
            models.Index(fields=["school", "due_on"]),
        ]
        ordering = ["due_on", "id"]

    def __str__(self):
        return f"{self.head_name} {paise_to_rupees(self.amount)}"

    @property
    def settled(self) -> int:
        return self.allocations.filter(
            payment__clearing_status=Payment.Clearing.CLEARED,
            payment__reversed_by__isnull=True,
        ).aggregate(total=Sum("amount"))["total"] or 0

    @property
    def outstanding(self) -> int:
        return self.amount - self.settled


class Concession(LedgerEntry):
    """Sibling, staff ward, RTE, merit. Reduces what is owed without a payment."""

    class Reason(models.TextChoices):
        SIBLING = "sibling", "Sibling discount"
        STAFF_WARD = "staff_ward", "Staff ward"
        RTE = "rte", "RTE quota"
        MERIT = "merit", "Merit scholarship"
        HARDSHIP = "hardship", "Financial hardship"
        OTHER = "other", "Other"

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="concessions"
    )
    fee_head = models.ForeignKey(
        FeeHead, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=15, choices=Reason.choices)
    note = models.CharField(max_length=200, blank=True)
    amount = MoneyField(validators=[MinValueValidator(0)])
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # RTE concessions are reimbursed by the state and must report separately.
    is_government_reimbursed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gte=0), name="concession_amount_non_negative"
            )
        ]


class Invoice(TenantScoped):
    """
    The demand note. Distinct from a receipt — this says what is owed, the
    receipt says what was received. The PDF is a rendering, not the truth:
    it must always be regenerable from these rows.
    """
    invoice_no = models.CharField(max_length=40)
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="invoices"
    )
    issued_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField()
    # Frozen for lawful reprint after the student record is de-identified.
    student_name_at_issue = models.CharField(max_length=150)
    class_at_issue = models.CharField(max_length=40)
    pdf_key = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "invoice_no"], name="uniq_invoice_no_per_school"
            )
        ]
        ordering = ["-issued_on"]

    def __str__(self):
        return self.invoice_no

    @property
    def total(self) -> int:
        return self.lines.filter(reversed_by__isnull=True).aggregate(
            total=Sum("amount")
        )["total"] or 0


class Payment(LedgerEntry):
    """
    Money received. receipt_no is gapless per school per financial year —
    see DocumentCounter. Never derive it from the primary key.
    """

    class Mode(models.TextChoices):
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        NETBANKING = "netbanking", "Net banking"
        NEFT = "neft", "NEFT / IMPS"
        CHEQUE = "cheque", "Cheque"
        DD = "dd", "Demand draft"

    class Clearing(models.TextChoices):
        PENDING = "pending", "Pending"      # cheque in clearing, gateway unconfirmed
        CLEARED = "cleared", "Cleared"      # only this counts toward balance
        BOUNCED = "bounced", "Bounced"
        FAILED = "failed", "Failed"

    receipt_no = models.CharField(max_length=40)
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="payments"
    )
    amount = MoneyField(validators=[MinValueValidator(1)])
    mode = models.CharField(max_length=12, choices=Mode.choices)
    clearing_status = models.CharField(
        max_length=10, choices=Clearing.choices, default=Clearing.PENDING
    )
    received_on = models.DateField(default=timezone.localdate)
    cleared_on = models.DateField(null=True, blank=True)
    instrument_ref = models.CharField(
        max_length=100, blank=True, help_text="Cheque no, UTR, UPI ref."
    )
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )

    # ---- Gateway fields. NEVER store card numbers here (RBI tokenisation). --
    gateway = models.CharField(max_length=30, blank=True)
    gateway_order_id = models.CharField(max_length=100, blank=True)
    gateway_payment_id = models.CharField(max_length=100, blank=True)
    # Passed to the parent, kept out of the fee ledger totals.
    convenience_fee = MoneyField(default=0, validators=[MinValueValidator(0)])
    settled_on = models.DateField(null=True, blank=True)
    # ------------------------------------------------------------------------

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "receipt_no"], name="uniq_receipt_no_per_school"
            ),
            # Webhook idempotency. Gateways retry, duplicate and reorder;
            # without this you will double-credit a parent.
            models.UniqueConstraint(
                fields=["gateway", "gateway_payment_id"],
                condition=~Q(gateway_payment_id=""),
                name="uniq_gateway_payment_id",
            ),
            models.CheckConstraint(
                check=Q(amount__gt=0), name="payment_amount_positive"
            ),
        ]
        indexes = [models.Index(fields=["school", "received_on"])]
        ordering = ["-received_on", "-created_at"]

    def __str__(self):
        return f"{self.receipt_no} {paise_to_rupees(self.amount)}"

    @property
    def allocated(self) -> int:
        return self.allocations.aggregate(total=Sum("amount"))["total"] or 0

    @property
    def unallocated(self) -> int:
        return self.amount - self.allocated


class Allocation(TenantScoped):
    """
    Splits one payment across specific charges. Partial payment is the norm in
    Indian schools, so a payment cannot simply point at an invoice.
    """
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="allocations"
    )
    charge = models.ForeignKey(
        Charge, on_delete=models.PROTECT, related_name="allocations"
    )
    amount = MoneyField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "charge"], name="uniq_allocation_per_payment_charge"
            ),
            models.CheckConstraint(
                check=Q(amount__gt=0), name="allocation_amount_positive"
            ),
        ]


class PromotionBatch(TenantScoped):
    """
    Preview -> adjust -> commit. Holding the batch id on every created
    Enrollment is what makes the whole rollover auditable and reversible
    while the target year is still in PLANNING.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        COMMITTED = "committed", "Committed"
        REVERSED = "reversed", "Reversed"

    from_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="promotions_out"
    )
    to_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="promotions_in"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    committed_at = models.DateTimeField(null=True, blank=True)
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )
    carry_forward_arrears = models.BooleanField(default=True)
    block_on_dues = models.BooleanField(
        default=False, help_text="School policy: refuse promotion if dues outstanding."
    )


# ---------------------------------------------------------------------
# Gapless document numbering
# ---------------------------------------------------------------------

class DocumentCounter(models.Model):
    """
    Receipt numbers must be sequential AND gapless per financial year for
    audit. An auto-increment PK leaves gaps on rollback and a UUID has no
    order, so neither is acceptable. Row lock inside the payment transaction.
    """

    class DocType(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        INVOICE = "invoice", "Invoice"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="+")
    doc_type = models.CharField(max_length=10, choices=DocType.choices)
    fiscal_year = models.CharField(max_length=10)       # "2026-27"
    prefix = models.CharField(max_length=20, blank=True)
    next_value = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "doc_type", "fiscal_year"], name="uniq_doc_counter"
            )
        ]

    @classmethod
    def issue(cls, *, school_id, doc_type, fiscal_year, prefix="") -> str:
        """
        Allocate the next number. MUST be called inside the same transaction
        that writes the Payment, or a rollback will burn a number.
        """
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError("DocumentCounter.issue() requires an atomic block.")

        cls.objects.get_or_create(
            school_id=school_id, doc_type=doc_type,
            fiscal_year=fiscal_year, defaults={"prefix": prefix},
        )
        counter = cls.objects.select_for_update().get(
            school_id=school_id, doc_type=doc_type, fiscal_year=fiscal_year
        )
        value = counter.next_value
        counter.next_value = value + 1
        counter.save(update_fields=["next_value"])
        return f"{counter.prefix}{fiscal_year}/{value:05d}"


# ---------------------------------------------------------------------
# DPDP consent layer
# ---------------------------------------------------------------------

class ConsentNotice(TenantScoped):
    """
    Versioned notice text. You must be able to prove what a parent actually
    saw and agreed to, not what your privacy page says today.
    """
    version = models.CharField(max_length=20)           # "v1.2"
    purpose = models.CharField(max_length=50)
    body_en = models.TextField()
    body_local = models.TextField(blank=True, help_text="Kannada / Hindi text.")
    effective_from = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "purpose", "version"], name="uniq_notice_version"
            )
        ]


class ConsentRecord(TenantScoped):
    """
    DPDP Section 9 / Rule 10. Every student here is a child under 18, so
    verifiable parental consent is required before processing — and it is
    per-purpose, not one blanket tick on the admission form.

    Consent is withdrawable: never delete a row, set withdrawn_at.
    """

    class Purpose(models.TextChoices):
        FEE_ADMIN = "fee_admin", "Fee administration"
        ACADEMIC_RECORDS = "academic_records", "Academic records"
        SMS_WHATSAPP = "sms_whatsapp", "SMS / WhatsApp communication"
        PHOTO_PUBLICATION = "photo_publication", "Photographs and publicity"
        TRANSPORT_TRACKING = "transport_tracking", "School bus tracking"

    class Method(models.TextChoices):
        DIGILOCKER = "digilocker", "DigiLocker verified token"
        IN_PERSON_ID = "in_person_id", "In-person ID check at office"
        SIGNED_FORM = "signed_form", "Wet-signed form on record"
        # A child typing a parent's email is NOT verifiable consent.

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="consents"
    )
    notice = models.ForeignKey(
        ConsentNotice, on_delete=models.PROTECT, related_name="records"
    )
    purpose = models.CharField(max_length=25, choices=Purpose.choices)
    granted_by_name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=40)       # "Father", "Legal guardian"
    verification_method = models.CharField(max_length=20, choices=Method.choices)
    verification_ref = models.CharField(
        max_length=200, blank=True,
        help_text="DigiLocker token or scanned form key. Never store raw Aadhaar.",
    )
    granted_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["school", "student", "purpose"])]

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None

    @classmethod
    def has_consent(cls, student_id, purpose) -> bool:
        return cls.objects.filter(
            student_id=student_id, purpose=purpose, withdrawn_at__isnull=True
        ).exists()
