"""
Bulk import of an existing student roll.

A school's first upload comes out of whatever they were using before — a
spreadsheet a clerk has maintained by hand for nine years. It will have
merged headers, blank rows, dates in four formats, duplicate admission
numbers and class names written five different ways.

So the pipeline is: parse -> map columns -> validate -> DRY RUN -> commit.
Nothing touches Student until an admin looks at the errors and confirms.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone

from fees.models import (
    AcademicYear,
    ClassLevel,
    Enrollment,
    ImportBatch,
    ImportRow,
    RouteStop,
    School,
    Section,
    Stream,
    Student,
)

log = logging.getLogger(__name__)

# What we need, and the header spellings seen in the wild.
FIELDS = {
    "admission_no": ["admission no", "admission number", "adm no", "adm.no",
                     "admno", "reg no", "registration no", "sl no"],
    "full_name": ["name", "student name", "name of student", "full name",
                  "student"],
    "class_name": ["class", "std", "standard", "grade", "class name"],
    "section": ["section", "sec", "div", "division"],
    "roll_no": ["roll no", "roll", "roll number"],
    "date_of_birth": ["dob", "date of birth", "birth date", "d.o.b"],
    "gender": ["gender", "sex"],
    "guardian_name": ["parent name", "father name", "guardian name",
                      "father's name", "parent", "guardian"],
    "guardian_phone": ["phone", "mobile", "contact", "phone no",
                       "mobile no", "contact number"],
    "guardian_email": ["email", "email id", "e-mail"],
    "address": ["address", "residential address"],
    "stream": ["stream", "combination", "group"],
    "bus_stop": ["bus stop", "stop", "transport stop", "pickup point"],
}

REQUIRED = ["admission_no", "full_name", "class_name"]

# "1st PU", "I PUC", "STD VIII", "8th", "Class 8" all mean one thing.
_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
    "viii": 8, "ix": 9, "x": 10,
}


class ImportError_(Exception):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower()).strip()


def suggest_column_map(headers: list[str]) -> dict:
    """
    Guess which uploaded column is which. The admin can override every guess
    — never import on a guess alone.
    """
    mapping = {}
    used = set()
    for field, aliases in FIELDS.items():
        for i, header in enumerate(headers):
            if i in used:
                continue
            h = _norm(header)
            if h == _norm(field) or h in [_norm(a) for a in aliases]:
                mapping[field] = i
                used.add(i)
                break
    return mapping


def normalise_class(raw: str) -> str:
    """
    Map a free-text class label onto a canonical ladder name.
    Returns "" when it cannot be resolved, which becomes a row error.
    """
    t = _norm(raw)
    if not t:
        return ""
    t = re.sub(r"^(class|std|standard|grade)\s+", "", t)
    t = re.sub(r"\s+(class|std|standard)$", "", t)

    if t in ("pre lkg", "prelkg", "pre kg", "prekg", "nursery", "play home",
             "playhome", "pre nursery"):
        return "Pre-LKG"
    if t in ("lkg", "l k g", "jr kg", "junior kg"):
        return "LKG"
    if t in ("ukg", "u k g", "sr kg", "senior kg"):
        return "UKG"

    if re.match(r"^(1|1st|i)\s*(pu|puc|pum)$", t):
        return "1st PU"
    if re.match(r"^(2|2nd|ii)\s*(pu|puc|pum)$", t):
        return "2nd PU"
    if t in ("11", "11th", "xi"):
        return "1st PU"
    if t in ("12", "12th", "xii"):
        return "2nd PU"

    m = re.match(r"^(\d{1,2})(st|nd|rd|th)?$", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 10:
            return {v: k.upper() for k, v in _ROMAN.items()}[n]
    if t in _ROMAN:
        return t.upper()
    return ""


def normalise_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) > 10 and digits.startswith("0"):
        digits = digits.lstrip("0")
    return digits


_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y",
    "%d/%m/%y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y",
]


def parse_date(raw: str):
    """
    Indian sheets are overwhelmingly day-first. %m/%d/%Y is tried last and
    only matches what day-first cannot, so 03/04/2015 reads as 3 April.
    """
    t = (raw or "").strip()
    if not t:
        return None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(t, fmt).date()
            if d.year > date.today().year:
                d = d.replace(year=d.year - 100)
            return d
        except ValueError:
            continue
    return None


def read_rows(content: str) -> tuple[list[str], list[list[str]]]:
    """Read CSV, skipping the blank and title rows real files start with."""
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(content), dialect)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        raise ImportError_("That file has no readable rows.")

    # The header is the first row with the most non-empty cells in the
    # first five — schools often put the school name on line 1.
    head_idx = max(range(min(5, len(rows))),
                   key=lambda i: sum(1 for c in rows[i] if (c or "").strip()))
    return rows[head_idx], rows[head_idx + 1:]


@transaction.atomic
def stage_import(
    *,
    school: School,
    academic_year: AcademicYear,
    filename: str,
    content: str,
    column_map: dict | None = None,
    created_by=None,
) -> ImportBatch:
    """Parse, validate and hold. Writes ImportRow only — never Student."""
    headers, body = read_rows(content)
    column_map = column_map or suggest_column_map(headers)

    missing = [f for f in REQUIRED if f not in column_map]
    if missing:
        raise ImportError_(
            "These columns could not be found: "
            + ", ".join(f.replace("_", " ") for f in missing)
            + ". Map them by hand and try again."
        )

    batch = ImportBatch.objects.create(
        school=school, academic_year=academic_year, filename=filename,
        column_map=column_map, total_rows=len(body), created_by=created_by,
    )

    known_classes = {
        c.name: c for c in ClassLevel.objects.filter(school=school)
    }
    existing_adm = set(
        Student.objects.filter(school=school).values_list("admission_no", flat=True)
    )
    seen_adm: set[str] = set()
    valid = 0
    staged = []

    for offset, row in enumerate(body, start=1):
        def cell(field):
            i = column_map.get(field)
            if i is None or i >= len(row):
                return ""
            return (row[i] or "").strip()

        errors, warnings = [], []
        raw = {f: cell(f) for f in column_map}

        adm = cell("admission_no")
        name = cell("full_name")
        klass_raw = cell("class_name")
        klass = normalise_class(klass_raw)

        if not adm:
            errors.append("Admission number is blank.")
        elif adm in seen_adm:
            errors.append(f"Admission number {adm} appears twice in this file.")
        elif adm in existing_adm:
            errors.append(f"Admission number {adm} is already in the system.")
        else:
            seen_adm.add(adm)

        if not name:
            errors.append("Student name is blank.")
        elif len(name) < 2:
            errors.append(f"Name '{name}' looks incomplete.")

        if not klass:
            errors.append(
                f"Could not read the class '{klass_raw}'. "
                "Use Pre-LKG, LKG, UKG, I to X, 1st PU or 2nd PU."
            )
        elif klass not in known_classes:
            errors.append(f"{klass} is not set up for this school yet.")

        section = (cell("section") or "A").upper()[:10]

        dob_raw = cell("date_of_birth")
        dob = parse_date(dob_raw)
        if dob_raw and not dob:
            warnings.append(f"Could not read the date '{dob_raw}'. Left blank.")

        phone = normalise_phone(cell("guardian_phone"))
        if cell("guardian_phone") and len(phone) != 10:
            warnings.append(
                f"Phone '{cell('guardian_phone')}' is not 10 digits. Kept as is."
            )
        if not cell("guardian_name"):
            warnings.append("No guardian name. Fee reminders will have no contact.")

        stop_name = cell("bus_stop")
        if stop_name and not RouteStop.objects.filter(
            school=school, name__iexact=stop_name
        ).exists():
            warnings.append(
                f"Bus stop '{stop_name}' is not on any route. Transport not assigned."
            )

        raw.update({
            "_class": klass, "_section": section, "_dob": dob.isoformat() if dob else "",
            "_phone": phone,
        })

        staged.append(ImportRow(
            school=school, batch=batch, line_no=offset,
            raw=raw, errors=errors, warnings=warnings,
        ))
        if not errors:
            valid += 1

    ImportRow.objects.bulk_create(staged)
    batch.valid_rows = valid
    batch.status = ImportBatch.Status.VALIDATED
    batch.save(update_fields=["valid_rows", "status"])

    log.info("Staged import %s: %d rows, %d valid", batch.id, len(body), valid)
    return batch


@transaction.atomic
def commit_import(batch: ImportBatch, *, skip_invalid: bool = True,
                  committed_by=None) -> dict:
    """
    Create students and enrollments from a validated batch.

    Deliberately does NOT generate charges. Importing a roll is a records
    exercise; billing them is a separate, explicit decision the admin makes
    once the fee structure is confirmed correct.
    """
    if batch.status != ImportBatch.Status.VALIDATED:
        raise ImportError_("This batch has already been committed or cancelled.")

    rows = list(batch.rows.all())
    bad = [r for r in rows if r.errors]
    if bad and not skip_invalid:
        raise ImportError_(
            f"{len(bad)} rows still have errors. Fix them or choose to skip."
        )

    year = batch.academic_year
    classes = {c.name: c for c in ClassLevel.objects.filter(school_id=batch.school_id)}
    streams = {
        s.name.lower(): s for s in Stream.objects.filter(school_id=batch.school_id)
    }
    created = 0

    for row in rows:
        if row.errors:
            continue

        klass = classes[row.raw["_class"]]
        section, _ = Section.objects.get_or_create(
            school_id=batch.school_id, academic_year=year,
            class_level=klass, name=row.raw["_section"],
            defaults={"capacity": 40},
        )

        student = Student.objects.create(
            school_id=batch.school_id,
            admission_no=row.raw.get("admission_no", ""),
            full_name=row.raw.get("full_name", ""),
            date_of_birth=row.raw["_dob"] or None,
            gender=row.raw.get("gender", ""),
            guardian_name=row.raw.get("guardian_name", ""),
            guardian_phone=row.raw["_phone"],
            guardian_email=row.raw.get("guardian_email", ""),
            address=row.raw.get("address", ""),
            created_by=committed_by,
        )

        stream = None
        if klass.requires_stream:
            key = (row.raw.get("stream") or "").strip().lower()
            stream = streams.get(key) or next(
                (s for k, s in streams.items() if key and key in k), None
            )

        enrollment = Enrollment.objects.create(
            school_id=batch.school_id, student=student, academic_year=year,
            class_level=klass, section=section, stream=stream,
            roll_no=int(row.raw["roll_no"]) if str(row.raw.get("roll_no", "")).isdigit() else None,
            # Imported students were already at the school; they are not
            # new admissions and must not be charged an admission fee.
            admission_type=Enrollment.AdmissionType.CARRY_OVER,
            created_by=committed_by,
        )
        row.student = student
        row.save(update_fields=["student"])
        created += 1

    batch.status = ImportBatch.Status.COMMITTED
    batch.committed_at = timezone.now()
    batch.save(update_fields=["status", "committed_at"])

    return {"created": created, "skipped": len(bad)}


def template_csv() -> str:
    """The blank sheet to hand a school that has nothing usable."""
    cols = ["Admission No", "Name", "Class", "Section", "Roll No",
            "DOB", "Gender", "Guardian Name", "Phone", "Email",
            "Address", "Stream", "Bus Stop"]
    example = ["2026/0001", "Ananya Krishnamurthy", "VIII", "A", "1",
               "14/03/2012", "F", "R. Krishnamurthy", "9845012345",
               "parent@example.com", "12 MG Road, Bengaluru", "", "Jayanagar 4th Block"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    w.writerow(example)
    return out.getvalue()
