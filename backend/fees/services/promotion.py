"""
The promotion engine: rolling a whole school up one rung of the ladder.

Three phases, never one button:

    preview()  -> proposed moves, with balances, nothing written
    (the office adjusts exclusions, sections and PUC streams)
    commit()   -> one transaction, tagged with a PromotionBatch id
    reverse()  -> undo, allowed only while the target year is PLANNING

The branch that breaks naive implementations: X -> 1st PUC is not automatic.
Many students leave for another board or college, and those who stay must
choose a stream. Any class level flagged requires_explicit_optin is excluded
from the default proposal and has to be opted in per student.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from fees.models import (
    AcademicYear,
    ClassLevel,
    Enrollment,
    PromotionBatch,
    Section,
    Student,
)
from fees.services.billing import carry_forward_arrears, generate_charges

log = logging.getLogger(__name__)


class PromotionError(Exception):
    pass


@dataclass
class ProposedMove:
    enrollment: Enrollment
    to_class: ClassLevel | None
    to_section: Section | None = None
    stream_id=None
    balance: int = 0
    needs_stream: bool = False
    needs_optin: bool = False
    blocked_reason: str = ""

    @property
    def is_actionable(self) -> bool:
        return (
            self.to_class is not None
            and not self.blocked_reason
            and not self.needs_optin
            and not (self.needs_stream and self.stream_id is None)
        )


@dataclass
class PromotionPreview:
    from_year: AcademicYear
    to_year: AcademicYear
    moves: list = field(default_factory=list)
    graduating: list = field(default_factory=list)
    blocked: list = field(default_factory=list)

    @property
    def total_arrears(self) -> int:
        return sum(m.balance for m in self.moves if m.balance > 0)

    def summary(self) -> dict:
        return {
            "promotable": sum(1 for m in self.moves if m.is_actionable),
            "needs_decision": sum(1 for m in self.moves if not m.is_actionable),
            "graduating": len(self.graduating),
            "blocked": len(self.blocked),
            "total_arrears_paise": self.total_arrears,
        }


def preview(
    *,
    from_year: AcademicYear,
    to_year: AcademicYear,
    block_on_dues: bool = False,
    exclude_enrollment_ids: set | None = None,
) -> PromotionPreview:
    """Compute proposed moves. Writes nothing."""
    if from_year.school_id != to_year.school_id:
        raise PromotionError("Cannot promote across schools.")
    if to_year.starts_on <= from_year.starts_on:
        raise PromotionError("Target year must follow the source year.")

    exclude = exclude_enrollment_ids or set()
    result = PromotionPreview(from_year=from_year, to_year=to_year)

    enrollments = (
        Enrollment.objects.filter(
            academic_year=from_year, is_active=True
        )
        .exclude(id__in=exclude)
        .exclude(
            outcome__in=[
                Enrollment.Outcome.TC_ISSUED,
                Enrollment.Outcome.LEFT,
            ]
        )
        .select_related("student", "class_level", "section", "stream")
        .order_by("class_level__ladder_order", "section__name", "roll_no")
    )

    for enrollment in enrollments:
        balance = enrollment.balance
        current = enrollment.class_level

        if enrollment.outcome == Enrollment.Outcome.DETAINED:
            result.blocked.append(
                ProposedMove(
                    enrollment=enrollment, to_class=current, balance=balance,
                    blocked_reason="Detained — repeats the same class.",
                )
            )
            continue

        if current.is_terminal:
            result.graduating.append(
                ProposedMove(
                    enrollment=enrollment, to_class=None, balance=balance,
                    blocked_reason="Completes schooling — becomes alumni.",
                )
            )
            continue

        next_class = current.next_level()
        if next_class is None:
            result.blocked.append(
                ProposedMove(
                    enrollment=enrollment, to_class=None, balance=balance,
                    blocked_reason=f"No class configured above {current.name}.",
                )
            )
            continue

        move = ProposedMove(
            enrollment=enrollment,
            to_class=next_class,
            balance=balance,
            needs_stream=next_class.requires_stream,
            needs_optin=next_class.requires_explicit_optin,
        )
        # Same stream carries forward automatically (1st PUC -> 2nd PUC).
        if next_class.requires_stream and enrollment.stream_id:
            move.stream_id = enrollment.stream_id
            move.needs_stream = False

        if block_on_dues and balance > 0:
            move.blocked_reason = (
                f"Outstanding dues of {balance / 100:.2f}; school policy blocks promotion."
            )
            result.blocked.append(move)
            continue

        result.moves.append(move)

    return result


def assign_sections(
    moves: list,
    *,
    to_year: AcademicYear,
    strategy: str = "keep",
) -> list:
    """
    Fill in to_section on each move.

    "keep"    — same section name if it exists in the target class, else balance.
    "balance" — round-robin into the least-full sections.

    Schools reshuffle sections deliberately, so this is always overridable
    per student in the adjust phase.
    """
    if strategy not in ("keep", "balance"):
        raise PromotionError(f"Unknown section strategy: {strategy}")

    sections_by_class: dict = {}
    counts: dict = {}

    for move in moves:
        if move.to_class is None:
            continue
        cid = move.to_class.id
        if cid not in sections_by_class:
            available = list(
                Section.objects.filter(
                    academic_year=to_year, class_level_id=cid
                ).order_by("name")
            )
            if not available:
                raise PromotionError(
                    f"No sections configured for {move.to_class.name} in {to_year.name}."
                )
            sections_by_class[cid] = available
            for s in available:
                counts[s.id] = Enrollment.objects.filter(section=s).count()

        available = sections_by_class[cid]

        chosen = None
        if strategy == "keep":
            same_name = next(
                (s for s in available if s.name == move.enrollment.section.name), None
            )
            if same_name and counts[same_name.id] < same_name.capacity:
                chosen = same_name

        if chosen is None:
            chosen = min(available, key=lambda s: (counts[s.id], s.name))

        move.to_section = chosen
        counts[chosen.id] += 1

    return moves


@transaction.atomic
def commit(
    *,
    from_year: AcademicYear,
    to_year: AcademicYear,
    moves: list,
    carry_arrears: bool = True,
    generate_new_charges: bool = True,
    committed_by=None,
) -> PromotionBatch:
    """
    Execute the rollover in a single transaction, tagged with a batch id.

    Order matters: the arrears carry-forward reads the OLD enrollment's
    balance, so it must run before any charges are posted to the new one.
    """
    if not to_year.is_editable:
        raise PromotionError(f"{to_year} is closed.")

    actionable = [m for m in moves if m.is_actionable]
    if not actionable:
        raise PromotionError("No actionable moves — nothing to commit.")

    missing = [m for m in actionable if m.to_section is None]
    if missing:
        raise PromotionError(
            f"{len(missing)} moves have no section assigned. Run assign_sections first."
        )

    batch = PromotionBatch.objects.create(
        school_id=from_year.school_id,
        from_year=from_year,
        to_year=to_year,
        status=PromotionBatch.Status.COMMITTED,
        committed_at=timezone.now(),
        committed_by=committed_by,
        created_by=committed_by,
        carry_forward_arrears=carry_arrears,
    )

    for move in actionable:
        old = move.enrollment

        new_enrollment = Enrollment.objects.create(
            school_id=old.school_id,
            student=old.student,
            academic_year=to_year,
            class_level=move.to_class,
            section=move.to_section,
            stream_id=move.stream_id,
            admission_type=Enrollment.AdmissionType.CARRY_OVER,
            outcome=Enrollment.Outcome.PENDING,
            promotion_batch=batch,
            created_by=committed_by,
        )

        # Read the old balance BEFORE new charges land on the student.
        if carry_arrears:
            carry_forward_arrears(
                from_enrollment=old,
                to_enrollment=new_enrollment,
                created_by=committed_by,
            )

        if generate_new_charges:
            generate_charges(new_enrollment, created_by=committed_by)

        old.outcome = Enrollment.Outcome.PROMOTED
        old.is_active = False
        old.save(update_fields=["outcome", "is_active"])

    # Terminal-class students exit to alumni rather than a new enrollment.
    for move in moves:
        if move.to_class is None and move.enrollment.class_level.is_terminal:
            e = move.enrollment
            e.outcome = Enrollment.Outcome.PASSED_OUT
            e.is_active = False
            e.save(update_fields=["outcome", "is_active"])
            Student.objects.filter(id=e.student_id).update(
                status=Student.Status.ALUMNI
            )

    log.info(
        "Promotion batch %s committed: %d students %s -> %s",
        batch.id, len(actionable), from_year.name, to_year.name,
    )
    return batch


@transaction.atomic
def reverse(batch: PromotionBatch) -> PromotionBatch:
    """
    Undo a rollover. Only while the target year is still PLANNING — once fees
    have been collected against the new year, unwinding would orphan receipts.
    """
    if batch.status != PromotionBatch.Status.COMMITTED:
        raise PromotionError("Only a committed batch can be reversed.")
    if batch.to_year.status != AcademicYear.Status.PLANNING:
        raise PromotionError(
            "Target year is no longer in planning; reverse is unsafe. "
            "Correct individual enrollments instead."
        )

    new_enrollments = Enrollment.objects.filter(promotion_batch=batch)

    paid = new_enrollments.filter(payments__isnull=False).exists()
    if paid:
        raise PromotionError(
            "Payments already recorded against this batch; cannot reverse."
        )

    for e in new_enrollments.select_related("student"):
        e.charges.all().delete()          # only structure/arrear rows, never paid
        previous = Enrollment.objects.filter(
            student=e.student, academic_year=batch.from_year
        ).first()
        if previous:
            previous.outcome = Enrollment.Outcome.PENDING
            previous.is_active = True
            previous.save(update_fields=["outcome", "is_active"])
        e.delete()

    batch.status = PromotionBatch.Status.REVERSED
    batch.save(update_fields=["status"])
    return batch
