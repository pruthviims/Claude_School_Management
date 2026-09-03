"""
Import tests use deliberately messy input, because that is what arrives:
a title row above the headers, class names in five notations, day-first
dates, phone numbers with +91 and spaces, and duplicate admission numbers.
"""

from datetime import date

from django.core.management import call_command
from django.test import TestCase

from fees.models import (
    AcademicYear,
    BusRoute,
    Enrollment,
    ImportBatch,
    RouteStop,
    School,
    Student,
)
from fees.services import importer

MESSY = """Vidya Mandir Public School - Student List 2026-27,,,,,,
Adm No,Name of Student,Std,Sec,D.O.B,Father's Name,Mobile No
2026/0001,Ananya Krishnamurthy,VIII,A,14/03/2012,R Krishnamurthy,+91 98450 12345
2026/0002,Rohan Reddy,8th,B,03/04/2012,S Reddy,9845012346
2026/0003,Meera Iyer,Class 8,A,2012-05-20,K Iyer,09845012347
2026/0004,Kiran Shetty,1st PU,A,11/07/2009,M Shetty,9845012348
2026/0005,Deepa Rao,II PUC,B,22/06/2008,N Rao,9845012349
2026/0006,Arjun Gowda,Pre-KG,A,09/09/2022,P Gowda,9845012350
2026/0007,,X,A,15/01/2010,T Nair,9845012351
2026/0001,Duplicate Child,IX,A,01/01/2011,X Person,9845012352
2026/0009,Sneha Bhat,Rocket Science,A,05/05/2011,V Bhat,notaphone
"""


class ImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_school", "--code", "imp", "--year", "2026-27",
                     verbosity=0)
        cls.school = School.objects.get(short_code="imp")
        cls.year = AcademicYear.objects.get(school=cls.school, name="2026-27")

    def stage(self, content=MESSY, name="roll.csv"):
        return importer.stage_import(
            school=self.school, academic_year=self.year,
            filename=name, content=content,
        )

    def test_header_detected_below_a_title_row(self):
        headers, body = importer.read_rows(MESSY)
        self.assertEqual(headers[0], "Adm No")
        self.assertEqual(len(body), 9)

    def test_columns_are_matched_from_real_world_header_names(self):
        headers, _ = importer.read_rows(MESSY)
        m = importer.suggest_column_map(headers)
        for field in ("admission_no", "full_name", "class_name", "section",
                      "date_of_birth", "guardian_name", "guardian_phone"):
            self.assertIn(field, m, f"{field} was not matched")

    def test_class_notations_all_resolve(self):
        cases = {
            "VIII": "VIII", "8th": "VIII", "Class 8": "VIII", "std viii": "VIII",
            "1st PU": "1st PU", "I PUC": "1st PU", "11th": "1st PU",
            "II PUC": "2nd PU", "12": "2nd PU",
            "Pre-KG": "Pre-LKG", "nursery": "Pre-LKG",
            "LKG": "LKG", "Sr KG": "UKG",
        }
        for raw, expected in cases.items():
            self.assertEqual(importer.normalise_class(raw), expected, raw)

    def test_unreadable_class_is_an_error_not_a_guess(self):
        self.assertEqual(importer.normalise_class("Rocket Science"), "")

    def test_dates_are_read_day_first(self):
        # 03/04/2012 is 3 April in an Indian sheet, not 4 March.
        self.assertEqual(importer.parse_date("03/04/2012"), date(2012, 4, 3))
        self.assertEqual(importer.parse_date("14/03/2012"), date(2012, 3, 14))
        self.assertEqual(importer.parse_date("2012-05-20"), date(2012, 5, 20))
        self.assertIsNone(importer.parse_date("not a date"))

    def test_phone_numbers_are_cleaned(self):
        self.assertEqual(importer.normalise_phone("+91 98450 12345"), "9845012345")
        self.assertEqual(importer.normalise_phone("09845012347"), "9845012347")

    def test_staging_writes_no_students(self):
        before = Student.objects.count()
        batch = self.stage()
        self.assertEqual(Student.objects.count(), before)
        self.assertEqual(batch.status, ImportBatch.Status.VALIDATED)
        self.assertEqual(batch.total_rows, 9)

    def test_bad_rows_are_flagged_with_a_readable_reason(self):
        batch = self.stage()
        by_line = {r.line_no: r for r in batch.rows.all()}

        self.assertIn("blank", by_line[7].errors[0].lower())        # no name
        self.assertIn("twice", by_line[8].errors[0].lower())        # duplicate
        self.assertIn("class", by_line[9].errors[0].lower())        # junk class

        self.assertEqual(batch.valid_rows, 6)

    def test_soft_problems_are_warnings_not_errors(self):
        batch = self.stage()
        row = batch.rows.get(line_no=9)
        self.assertTrue(
            any("10 digits" in w for w in row.warnings),
            "A bad phone should warn, not block the row.",
        )

    def test_commit_creates_students_and_enrollments(self):
        batch = self.stage()
        result = importer.commit_import(batch, skip_invalid=True)

        self.assertEqual(result["created"], 6)
        self.assertEqual(result["skipped"], 3)
        self.assertEqual(Student.objects.filter(school=self.school).count(), 6)

        e = Enrollment.objects.get(student__admission_no="2026/0002")
        self.assertEqual(e.class_level.name, "VIII")
        self.assertEqual(e.section.name, "B")

    def test_imported_students_are_not_charged_an_admission_fee(self):
        """They were already at the school. This is the costly mistake."""
        batch = self.stage()
        importer.commit_import(batch, skip_invalid=True)
        e = Enrollment.objects.get(student__admission_no="2026/0001")
        self.assertEqual(e.admission_type, Enrollment.AdmissionType.CARRY_OVER)

    def test_commit_can_refuse_when_rows_are_broken(self):
        batch = self.stage()
        with self.assertRaises(importer.ImportError_):
            importer.commit_import(batch, skip_invalid=False)

    def test_a_batch_cannot_be_committed_twice(self):
        batch = self.stage()
        importer.commit_import(batch, skip_invalid=True)
        with self.assertRaises(importer.ImportError_):
            importer.commit_import(batch, skip_invalid=True)

    def test_second_import_rejects_admission_numbers_already_in_the_system(self):
        importer.commit_import(self.stage(), skip_invalid=True)
        again = self.stage()
        row = again.rows.get(line_no=1)
        self.assertTrue(any("already in the system" in e for e in row.errors))

    def test_unknown_bus_stop_warns_without_blocking(self):
        batch = self.stage()
        route = BusRoute.objects.create(
            school=self.school, code="R-01", name="Jayanagar"
        )
        RouteStop.objects.create(
            school=self.school, route=route, name="4th Block", sequence=1
        )
        content = MESSY.replace(
            "Mobile No", "Mobile No,Bus Stop"
        ).replace("+91 98450 12345", "+91 98450 12345,Nowhere Junction")
        batch = self.stage(content=content)
        row = batch.rows.get(line_no=1)
        self.assertEqual(row.errors, [])
        self.assertTrue(any("not on any route" in w for w in row.warnings))

    def test_missing_required_column_is_refused_upfront(self):
        with self.assertRaises(importer.ImportError_) as ctx:
            self.stage(content="Name,Class\nAsha,VIII\n")
        self.assertIn("admission no", str(ctx.exception).lower())

    def test_template_round_trips(self):
        batch = self.stage(content=importer.template_csv(), name="template.csv")
        self.assertEqual(batch.total_rows, 1)
        self.assertEqual(batch.valid_rows, 1)
