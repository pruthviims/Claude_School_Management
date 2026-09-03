"""
Receipt and bill rendering.

The PDF is a RENDERING, never the source of truth. Everything needed to
reproduce it byte-for-byte lives in the database, so a lost object-store
object is an inconvenience rather than a lost financial record.

WeasyPrint is used rather than headless Chrome: pure Python, no browser
binary to keep patched, which is one fewer CVE stream in a system holding
children's data.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string

from fees.models import Invoice, Payment, paise_to_rupees

log = logging.getLogger(__name__)

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
]


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _under_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} hundred")
    if rest:
        parts.append(_under_hundred(rest))
    return " ".join(parts)


def amount_in_words(paise: int) -> str:
    """
    Indian numbering: lakh and crore, not million and billion.

    Every off-the-shelf library gives you "one million two hundred thousand"
    and the school's accountant will send the receipt back. This is why it is
    written by hand.

        1234567 rupees -> "twelve lakh thirty-four thousand five hundred
                           sixty-seven rupees only"
    """
    rupees_dec = paise_to_rupees(paise)
    rupees = int(rupees_dec)
    paise_part = int((rupees_dec - rupees) * 100)

    if rupees == 0:
        words = "zero"
    else:
        crore, rest = divmod(rupees, 10_000_000)
        lakh, rest = divmod(rest, 100_000)
        thousand, rest = divmod(rest, 1_000)

        chunks = []
        if crore:
            chunks.append(f"{_under_thousand(crore)} crore")
        if lakh:
            chunks.append(f"{_under_thousand(lakh)} lakh")
        if thousand:
            chunks.append(f"{_under_thousand(thousand)} thousand")
        if rest:
            chunks.append(_under_thousand(rest))
        words = " ".join(chunks)

    out = f"{words} rupees"
    if paise_part:
        out += f" and {_under_hundred(paise_part)} paise"
    return (out + " only").capitalize()


def format_inr(paise: int) -> str:
    """Indian digit grouping: 12,34,567.00 not 1,234,567.00."""
    value = paise_to_rupees(paise)
    negative = value < 0
    whole, frac = divmod(abs(value), 1)
    whole = int(whole)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    result = f"{s}.{int(frac * 100):02d}"
    return f"-{result}" if negative else result


def _context(school, *, heading, doc_no, doc_date, enrollment, lines,
             total, is_duplicate, payment=None):
    return {
        "school": school,
        "heading": heading,
        "doc_no": doc_no,
        "doc_date": doc_date,
        "student": enrollment.student,
        "enrollment": enrollment,
        "lines": lines,
        "total": total,
        "total_display": format_inr(total),
        "total_words": amount_in_words(total),
        "payment": payment,
        "is_duplicate": is_duplicate,
        "currency": settings.FEES["CURRENCY_SYMBOL"],
        "format_inr": format_inr,
    }


def render_receipt_html(payment: Payment, *, is_duplicate: bool = False) -> str:
    lines = [
        {
            "name": a.charge.head_name,
            "term": a.charge.term_no,
            "amount": a.amount,
            "amount_display": format_inr(a.amount),
        }
        for a in payment.allocations.select_related("charge").all()
    ]
    unallocated = payment.unallocated
    if unallocated > 0:
        lines.append({
            "name": "Advance (unallocated)",
            "term": "",
            "amount": unallocated,
            "amount_display": format_inr(unallocated),
        })

    return render_to_string(
        "fees/document.html",
        _context(
            payment.enrollment.school,
            heading="Fee receipt",
            doc_no=payment.receipt_no,
            doc_date=payment.received_on,
            enrollment=payment.enrollment,
            lines=lines,
            total=payment.amount,
            is_duplicate=is_duplicate,
            payment=payment,
        ),
    )


def render_invoice_html(invoice: Invoice, *, is_duplicate: bool = False) -> str:
    lines = [
        {
            "name": c.head_name,
            "term": c.term_no,
            "amount": c.amount,
            "amount_display": format_inr(c.amount),
        }
        for c in invoice.lines.filter(reversed_by__isnull=True)
    ]
    return render_to_string(
        "fees/document.html",
        _context(
            invoice.enrollment.school,
            heading="Fee bill",
            doc_no=invoice.invoice_no,
            doc_date=invoice.issued_on,
            enrollment=invoice.enrollment,
            lines=lines,
            total=invoice.total,
            is_duplicate=is_duplicate,
        ),
    )


def html_to_pdf(html: str) -> bytes:
    """
    WeasyPrint import is deferred so the test suite and the API run without
    the native cairo/pango stack installed.
    """
    try:
        from weasyprint import HTML
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "WeasyPrint is not installed. `pip install weasyprint` and ensure "
            "libpango/libcairo are present (apt install libpango-1.0-0 "
            "libpangoft2-1.0-0)."
        ) from exc
    return HTML(string=html).write_pdf()


def receipt_pdf(payment: Payment, *, is_duplicate: bool = False) -> bytes:
    return html_to_pdf(render_receipt_html(payment, is_duplicate=is_duplicate))


def invoice_pdf(invoice: Invoice, *, is_duplicate: bool = False) -> bytes:
    return html_to_pdf(render_invoice_html(invoice, is_duplicate=is_duplicate))
