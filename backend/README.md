# School fee management — backend

Django + PostgreSQL backend for a school fee system covering LKG through
2nd PUC. Handles admissions, termwise fee billing, receipt and bill PDFs,
partial payments, online collection via a payment gateway, and year-end
promotion with arrears carry-forward.

Built for India: integer paise, Indian digit grouping and amount-in-words,
April–March financial years, GST-exempt education services, and the DPDP
consent obligations that apply because every data subject is a minor.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_school --name "Your School" --code yourschool --year 2026-27
python manage.py createsuperuser
python manage.py runserver
```

The seed builds the full 14-rung ladder (LKG, UKG, I–X, 1st PUC, 2nd PUC),
three sections per class, the five Karnataka PUC stream combinations, nine
fee heads and 123 fee-structure lines.

Run the tests:

```bash
python manage.py test fees
```

WeasyPrint needs native libraries for PDF output:

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2
```

## The five invariants

Everything else is negotiable. These are not.

1. **Money is integer paise.** `MoneyField` is a `BigIntegerField`. Convert at
   the API boundary with `rupees_to_paise()`, never inside the domain.
2. **Class and section live on `Enrollment`, never on `Student`.** A student is
   permanent identity; an enrollment is one student in one year. This is what
   makes history, reprints and year-over-year reporting possible.
3. **The ledger is append-only.** `Charge`, `Concession`, `Payment` and
   `Allocation` are never mutated after posting. Corrections are reversing
   rows. The admin registers them read-only to enforce this.
4. **Every table carries `school_id`.** One school today, RLS tomorrow. Apply
   `sql/rls.sql` when you go multi-tenant.
5. **Balance is derived.** `Enrollment.ledger()` computes it. There is no
   mutable balance column and there must never be one.

## Layout

```
config/          settings, urls, wsgi
fees/
  models.py      the schema, with the invariants documented inline
  services/
    billing.py     fee-structure snapshot, invoicing, arrears carry-forward
    collection.py  payments, allocation, cheque clearing, gateway webhooks
    promotion.py   preview -> adjust -> commit -> reverse
    receipts.py    HTML rendering, Indian number formatting, PDF output
  views.py       REST API
  admin.py       office screens; financial models read-only
  templates/     A5 landscape receipt and bill
  tests/         24 tests pinning the invariants above
sql/rls.sql      Postgres row-level security for the SaaS phase
```

## How the pieces behave

**Billing.** `generate_charges()` snapshots the fee structure onto an
enrollment. Editing `FeeStructure` afterwards does not change what an existing
student owes — there is a test for exactly this. One-time heads (admission fee)
only apply to `admission_type=NEW`. Optional heads (transport, hostel) must be
opted into explicitly. The function is idempotent, so a half-failed admission
can be safely retried.

**Collection.** Payments split into allocations across specific charges,
arrears first then oldest due. Cash, UPI, card and net banking clear
immediately; cheques and DDs sit `PENDING` and do not affect the balance until
`mark_cleared()`. `mark_bounced()` drops the allocations but keeps the payment
row, because the receipt number was issued and must stay in the sequence.

**Receipt numbering.** `DocumentCounter.issue()` takes a row lock and hands out
the next value. It raises if called outside a transaction, because a rollback
would burn a number and leave a gap an auditor will ask about. Never derive a
receipt number from a primary key.

**Online payments.** The webhook is the source of truth, never the browser
redirect. Signature is HMAC-SHA256 over the raw request body — re-serialising
parsed JSON changes key order and every signature fails. Idempotency is
enforced by a unique constraint on `(gateway, gateway_payment_id)` and the
handler catches `IntegrityError` rather than checking first, because
check-then-insert is exactly the race concurrent retries produce.

**Promotion.** Three phases: `preview()` proposes moves and writes nothing,
`assign_sections()` fills in targets (keep-same-name or round-robin balance),
`commit()` runs the whole rollover in one transaction tagged with a
`PromotionBatch`. `reverse()` undoes it, but only while the target year is
still `PLANNING` and no payments have landed.

The branch that matters: X → 1st PUC is flagged `requires_explicit_optin`, so
it never happens by default — many students leave for another board after X.
1st PUC → 2nd PUC carries the stream forward automatically. 2nd PUC is
terminal and routes to alumni.

Arrears carry forward as a single `is_arrear=True` charge tagged with the
source year, so current dues and old dues report separately.

## Compliance notes

The school is the Data Fiduciary under the DPDP Act; if you turn this into a
SaaS product you become a Data Processor and need a signed DPA per school plus
the ability to fully purge a tenant.

- `ConsentRecord` is per-purpose and withdrawable, tied to a versioned
  `ConsentNotice` so you can prove what a parent actually agreed to. Fee
  administration, WhatsApp reminders and photo publication are three separate
  consents, not one tickbox.
- `Student.deidentify()` strips the personal layer and leaves the ledger
  intact. `Invoice` freezes `student_name_at_issue` so past receipts stay
  lawfully reprintable after erasure.
- Do not add Google Analytics, Mixpanel, or any ad SDK. Behavioural tracking
  of children is prohibited, not merely discouraged.
- Host in `ap-south-1`. Do not replicate cross-border until the Central
  Government notifies the approved country list under Section 16.
- Full DPDP compliance is due 13 May 2027; enforcement powers activate
  13 November 2026.

Get privacy counsel to review before go-live, particularly on whether the
Fourth Schedule educational-institution exemption covers any of your
processing. I would not assume it does.

## Not built yet

- React frontend (the API and admin are there; the office UI is not)
- Live Razorpay order creation — `views.gateway_webhook` handles the inbound
  side, the outbound `create_order` call is a thin wrapper still to write
- Settlement reconciliation job against the gateway's payout report
- `django-auditlog` and TOTP MFA are in requirements but not yet wired
- WhatsApp/SMS reminders, parent portal
- Transport route/slab table (the fee head exists, the slab pricing does not)

## Production checklist

- [ ] `DJANGO_SECRET_KEY` from a secrets manager, never in the repo
- [ ] PostgreSQL with encryption at rest and PITR enabled from day one
- [ ] Apply `sql/rls.sql` and connect as a non-superuser role
- [ ] TOTP MFA on every accountant and admin account
- [ ] S3 with SSE-KMS for PDFs, private bucket, presigned URLs only
- [ ] Nightly settlement reconciliation before the office opens
- [ ] Breach-response runbook: Rule 7 requires notifying affected individuals
      within 72 hours
- [ ] Audit log retention of at least one year per Rule 6
