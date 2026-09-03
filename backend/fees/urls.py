from django.urls import path

from fees import views

urlpatterns = [
    path("admissions/", views.admit_student, name="admit-student"),
    path("enrollments/<uuid:enrollment_id>/ledger/", views.enrollment_ledger,
         name="enrollment-ledger"),
    path("enrollments/<uuid:enrollment_id>/bill/", views.issue_bill,
         name="issue-bill"),
    path("payments/", views.collect_payment, name="collect-payment"),
    path("payments/<uuid:payment_id>/receipt.pdf", views.receipt_pdf,
         name="receipt-pdf"),
    path("invoices/<uuid:invoice_id>/bill.pdf", views.bill_pdf, name="bill-pdf"),
    path("promotion/preview/", views.promotion_preview, name="promotion-preview"),
    path("webhooks/gateway/", views.gateway_webhook, name="gateway-webhook"),
    path("reports/day-book/", views.day_book, name="day-book"),
    path("reports/defaulters/", views.defaulters, name="defaulters"),
]
