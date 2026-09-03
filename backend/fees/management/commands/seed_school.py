"""
Seed a school with the full LKG -> 2nd PUC ladder, sections, streams and a
sample fee structure.

    python manage.py seed_school --name "St. Xavier's" --year 2026-27
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from fees.models import (
    AcademicYear,
    ClassLevel,
    ConsentNotice,
    FeeHead,
    FeeStructure,
    School,
    Section,
    Stream,
    rupees_to_paise,
)

STAGE = ClassLevel.Stage

# (name, ladder_order, stage, requires_stream, requires_explicit_optin, terminal)
LADDER = [
    ("Pre-LKG", 1,  STAGE.PRE_PRIMARY, False, False, False),
    ("LKG",     2,  STAGE.PRE_PRIMARY, False, False, False),
    ("UKG",     3,  STAGE.PRE_PRIMARY, False, False, False),
    ("I",       4,  STAGE.PRIMARY,     False, False, False),
    ("II",      5,  STAGE.PRIMARY,     False, False, False),
    ("III",     6,  STAGE.PRIMARY,     False, False, False),
    ("IV",      7,  STAGE.PRIMARY,     False, False, False),
    ("V",       8,  STAGE.PRIMARY,     False, False, False),
    ("VI",      9,  STAGE.MIDDLE,      False, False, False),
    ("VII",    10,  STAGE.MIDDLE,      False, False, False),
    ("VIII",   11,  STAGE.MIDDLE,      False, False, False),
    ("IX",     12,  STAGE.SECONDARY,   False, False, False),
    ("X",      13,  STAGE.SECONDARY,   False, False, False),
    # X -> 1st PU is the branch point: explicit opt-in plus a stream choice,
    # because many students leave for another board or college after X.
    ("1st PU", 14,  STAGE.PUC,         True,  True,  False),
    ("2nd PU", 15,  STAGE.PUC,         True,  False, True),
]

STREAMS = [
    ("Science", "PCMB"),
    ("Science", "PCMC"),
    ("Commerce", "SEBA"),
    ("Commerce", "ABMS"),
    ("Arts", "HEPS"),
]

# (name, basis, one_time, optional, display_order)
FEE_HEADS = [
    ("Admission fee",   FeeHead.Basis.FLAT,      True,  False, 1),
    ("Tuition fee",     FeeHead.Basis.PER_CLASS, False, False, 2),
    ("Development fee", FeeHead.Basis.PER_CLASS, False, False, 3),
    ("Lab fee",         FeeHead.Basis.PER_CLASS, False, False, 4),
    ("Computer fee",    FeeHead.Basis.PER_CLASS, False, False, 5),
    ("Library fee",     FeeHead.Basis.FLAT,      False, False, 6),
    ("Exam fee",        FeeHead.Basis.PER_CLASS, False, False, 7),
    ("Transport fee",   FeeHead.Basis.PER_SLAB,  False, True,  8),
    ("Hostel fee",      FeeHead.Basis.PER_SLAB,  False, True,  9),
]

# Annual tuition by stage, split across three terms.
TUITION_BY_STAGE = {
    STAGE.PRE_PRIMARY: 24000,
    STAGE.PRIMARY:     32000,
    STAGE.MIDDLE:      40000,
    STAGE.SECONDARY:   52000,
    STAGE.PUC:         68000,
}


class Command(BaseCommand):
    help = "Seed a school with the class ladder, sections and a fee structure."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Demo Public School")
        parser.add_argument("--code", default="demo")
        parser.add_argument("--year", default="2026-27")
        parser.add_argument("--sections", default="A,B,C")

    @transaction.atomic
    def handle(self, *args, **opts):
        school, _ = School.objects.get_or_create(
            short_code=opts["code"],
            defaults={
                "name": opts["name"],
                "address": "12 MG Road\nBengaluru, Karnataka 560001",
            },
        )
        self.stdout.write(self.style.SUCCESS(f"School: {school.name}"))

        start_year = int(opts["year"].split("-")[0])
        year, _ = AcademicYear.objects.get_or_create(
            school=school,
            name=opts["year"],
            defaults={
                "starts_on": date(start_year, 6, 1),
                "ends_on": date(start_year + 1, 3, 31),
                "status": AcademicYear.Status.ACTIVE,
            },
        )

        levels = {}
        for name, order, stage, stream, optin, terminal in LADDER:
            level, _ = ClassLevel.objects.get_or_create(
                school=school,
                ladder_order=order,
                defaults={
                    "name": name,
                    "stage": stage,
                    "requires_stream": stream,
                    "requires_explicit_optin": optin,
                    "is_terminal": terminal,
                },
            )
            levels[name] = level

        section_names = [s.strip() for s in opts["sections"].split(",")]
        made = 0
        for level in levels.values():
            for sname in section_names:
                _, created = Section.objects.get_or_create(
                    school=school,
                    academic_year=year,
                    class_level=level,
                    name=sname,
                    defaults={"capacity": 40},
                )
                made += int(created)

        for sname, comb in STREAMS:
            Stream.objects.get_or_create(
                school=school, name=sname, combination=comb
            )

        heads = {}
        for name, basis, one_time, optional, order in FEE_HEADS:
            head, _ = FeeHead.objects.get_or_create(
                school=school,
                name=name,
                defaults={
                    "basis": basis,
                    "is_one_time": one_time,
                    "is_optional": optional,
                    "display_order": order,
                },
            )
            heads[name] = head

        term_due = {
            1: date(start_year, 6, 10),
            2: date(start_year, 9, 10),
            3: date(start_year, 12, 10),
        }

        lines = 0
        for level in levels.values():
            annual_tuition = TUITION_BY_STAGE[level.stage]
            per_term = annual_tuition // 3

            for term in (1, 2, 3):
                lines += self._structure(
                    school, year, level, heads["Tuition fee"],
                    per_term, term, term_due[term]
                )

            lines += self._structure(
                school, year, level, heads["Development fee"],
                4000, 1, term_due[1]
            )
            lines += self._structure(
                school, year, level, heads["Library fee"],
                800, 1, term_due[1]
            )
            lines += self._structure(
                school, year, level, heads["Exam fee"],
                1500, 2, term_due[2]
            )
            lines += self._structure(
                school, year, level, heads["Admission fee"],
                15000, 1, term_due[1]
            )

            if level.stage in (STAGE.MIDDLE, STAGE.SECONDARY, STAGE.PUC):
                lines += self._structure(
                    school, year, level, heads["Computer fee"],
                    2500, 1, term_due[1]
                )

            # Lab fee only for PUC Science streams — priced per stream, which
            # is why FeeStructure has a nullable stream FK.
            if level.stage == STAGE.PUC:
                for stream in Stream.objects.filter(school=school, name="Science"):
                    lines += self._structure(
                        school, year, level, heads["Lab fee"],
                        9000, 1, term_due[1], stream=stream
                    )

            lines += self._structure(
                school, year, level, heads["Transport fee"],
                6000, 1, term_due[1]
            )

        ConsentNotice.objects.get_or_create(
            school=school,
            purpose="fee_admin",
            version="v1.0",
            defaults={
                "body_en": (
                    "We collect your child's name, class and guardian contact "
                    "details solely to administer school fees, issue receipts "
                    "and meet statutory accounting obligations. We do not "
                    "profile children or use this data for advertising. You "
                    "may withdraw this consent at any time by writing to the "
                    "school office."
                ),
                "effective_from": date(start_year, 4, 1),
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"Ladder: {len(levels)} classes | sections created: {made} | "
            f"fee structure lines: {lines} | year: {year.name}"
        ))

    def _structure(self, school, year, level, head, rupees, term, due, stream=None):
        _, created = FeeStructure.objects.get_or_create(
            school=school,
            academic_year=year,
            class_level=level,
            fee_head=head,
            stream=stream,
            term_no=term,
            defaults={"amount": rupees_to_paise(rupees), "due_on": due},
        )
        return int(created)
