"""
Django admin gives the school office most of its master-data screens free.

Financial rows are registered read-only on purpose: the ledger is append-only
and must be corrected through the service layer (which writes reversal rows),
never by editing a row in the admin.
"""

from django.contrib import admin

from fees import models


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "short_code", "is_active")


@admin.register(models.AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "starts_on", "ends_on", "status")
    list_filter = ("status",)


@admin.register(models.ClassLevel)
class ClassLevelAdmin(admin.ModelAdmin):
    list_display = ("ladder_order", "name", "stage", "requires_stream",
                    "requires_explicit_optin", "is_terminal")
    ordering = ("ladder_order",)


@admin.register(models.Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("class_level", "name", "academic_year", "capacity")
    list_filter = ("academic_year", "class_level")


@admin.register(models.Stream)
class StreamAdmin(admin.ModelAdmin):
    list_display = ("name", "combination", "applies_to_stage")


@admin.register(models.FeeHead)
class FeeHeadAdmin(admin.ModelAdmin):
    list_display = ("display_order", "name", "basis", "is_one_time",
                    "is_optional", "is_refundable")
    ordering = ("display_order",)


@admin.register(models.FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "class_level", "fee_head", "stream",
                    "term_no", "amount", "due_on")
    list_filter = ("academic_year", "class_level", "fee_head")


@admin.register(models.Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("admission_no", "full_name", "status", "deidentified_at")
    search_fields = ("admission_no", "full_name", "guardian_phone")
    list_filter = ("status",)


@admin.register(models.Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "academic_year", "section", "stream",
                    "admission_type", "outcome", "is_active")
    list_filter = ("academic_year", "class_level", "outcome", "admission_type")
    search_fields = ("student__admission_no", "student__full_name")
    raw_id_fields = ("student",)


@admin.register(models.Charge)
class ChargeAdmin(ReadOnlyAdmin):
    list_display = ("enrollment", "head_name", "amount", "term_no",
                    "due_on", "is_arrear")
    list_filter = ("is_arrear", "source", "term_no")


@admin.register(models.Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("receipt_no", "enrollment", "amount", "mode",
                    "clearing_status", "received_on")
    list_filter = ("mode", "clearing_status", "received_on")
    search_fields = ("receipt_no", "instrument_ref", "gateway_payment_id")


@admin.register(models.Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    list_display = ("invoice_no", "enrollment", "issued_on", "due_on")
    search_fields = ("invoice_no",)


@admin.register(models.Allocation)
class AllocationAdmin(ReadOnlyAdmin):
    list_display = ("payment", "charge", "amount")


@admin.register(models.Concession)
class ConcessionAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "reason", "amount",
                    "is_government_reimbursed", "approved_by")
    list_filter = ("reason", "is_government_reimbursed")


@admin.register(models.PromotionBatch)
class PromotionBatchAdmin(ReadOnlyAdmin):
    list_display = ("from_year", "to_year", "status", "committed_at",
                    "committed_by")


@admin.register(models.ConsentNotice)
class ConsentNoticeAdmin(admin.ModelAdmin):
    list_display = ("purpose", "version", "effective_from")


@admin.register(models.ConsentRecord)
class ConsentRecordAdmin(ReadOnlyAdmin):
    list_display = ("student", "purpose", "granted_by_name",
                    "verification_method", "granted_at", "withdrawn_at")
    list_filter = ("purpose", "verification_method")
