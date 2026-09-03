# Claude School Management

A school fee management system covering LKG through 2nd PUC (India), built
across two conversation turns with Claude. Two independent codebases in this
repo:

## `backend/`

Django + DRF backend: admissions, per-term fee billing, receipt/bill PDFs
(WeasyPrint), partial payments with allocation, online payment gateway
webhook handling, class-ladder promotion with arrears carry-forward, a
student CSV import pipeline, DPDP consent tracking, and Postgres row-level
security for future multi-tenancy. See `backend/README.md`.

```bash
cd backend
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_school --name "Your School" --code yourschool --year 2026-27
python manage.py test fees
```

## `admin-console/`

React + Vite + Tailwind v4 admin portal prototype: school/admin setup with a
setup-token gate, bus routes with per-stop fares, a fee-structure editor with
transport as a dynamic per-student component, class-scoped CSV student
import, and per-student fee concessions. State is kept in browser
localStorage — it is a design/workflow prototype, not yet wired to the
Django API.

```bash
cd admin-console
npm install
npm run dev
```

## Status

- Backend: 41 tests passing (billing, promotion, import, gateway idempotency).
- Admin console: functional prototype, not yet connected to the backend API.
- Not yet connected: the console's fee-structure/routes/concessions data
  model isn't wired to `backend`'s Django models — that's the next step
  before this can go live.
