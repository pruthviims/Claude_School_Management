import React, { useEffect, useState } from "react";
import {
  Bus,
  LogOut,
  Percent,
  ReceiptIndianRupee,
  RefreshCw,
  School,
  Sparkles,
  Users,
} from "lucide-react";
import { Login, Setup } from "./Auth";
import {
  AdmissionScreen,
  ConcessionScreen,
  FeeScreen,
  FilterSelect,
  ImportScreen,
  PaymentModal,
  SchoolScreen,
  TransportScreen,
} from "./Screens";
import { ACADEMIC_YEARS, CLASSES, TRANSPORT_ID } from "./lib";

const KEY = "school-fee-admin-v3";

const TUITION_BY_STAGE = {
  "Pre-primary": 24000, Primary: 32000, Middle: 40000,
  Secondary: 52000, "Pre-university": 68000,
};

let seq = 0;
const uid = () => `id${Date.now().toString(36)}${(seq += 1)}`;

function defaultComponents(stage) {
  const annual = TUITION_BY_STAGE[stage];
  const per = Math.round(annual / 3);
  const rows = [
    { id: uid(), name: "Tuition fee", terms: [per, per, annual - 2 * per], oneTime: false },
    { id: uid(), name: "Development fee", terms: [4000, 0, 0], oneTime: false },
    { id: uid(), name: "Library fee", terms: [800, 0, 0], oneTime: false },
    { id: uid(), name: "Exam fee", terms: [0, 1500, 0], oneTime: false },
    { id: uid(), name: "Admission fee", terms: [15000, 0, 0], oneTime: true },
  ];
  if (["Middle", "Secondary", "Pre-university"].includes(stage))
    rows.splice(3, 0, { id: uid(), name: "Computer fee", terms: [2500, 0, 0], oneTime: false });
  if (stage === "Pre-university")
    rows.splice(3, 0, { id: uid(), name: "Lab fee", terms: [9000, 0, 0], oneTime: false });
  // Transport belongs to every class; the amount comes from the child's stop.
  rows.push({ id: TRANSPORT_ID, name: "Transport fee", terms: [0, 0, 0], oneTime: false });
  return rows;
}

function freshWorkspace(school) {
  const structure = {};
  for (const c of CLASSES) structure[c.name] = defaultComponents(c.stage);
  return { school, year: "2026-27", structure, routes: [], students: [], payments: [] };
}

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Students saved before per-year tagging existed have no `year` field.
    // Backfill them to whatever year was active when they were saved, so
    // year-based filtering elsewhere never silently drops old data.
    if (parsed?.students?.some((s) => !s.year)) {
      parsed.students = parsed.students.map((s) => ({ ...s, year: s.year || parsed.year }));
    }
    // Payments didn't exist before receipt collection was added.
    if (!Array.isArray(parsed.payments)) parsed.payments = [];
    return parsed;
  } catch {
    /* unreadable storage falls through to a fresh start */
  }
  return null;
}

// Bus Routes and Fee Structure share one sidebar entry — routes have to
// exist before a fare can be attached to a class, so keeping them one click
// apart makes that dependency obvious rather than splitting it across the
// sidebar.
const NAV = [
  { id: "school", label: "School Profile", Icon: School },
  { id: "feesSetup", label: "Fees Setup", Icon: ReceiptIndianRupee,
    group: ["transport", "fees"] },
  { id: "admissions", label: "Admissions", Icon: Sparkles },
  { id: "import", label: "Student Records", Icon: Users },
  { id: "concessions", label: "Fees & Concessions", Icon: Percent },
];

// Screens where the working Academic Year actually matters. School Profile
// manages the year as school metadata via its own field, and Admissions
// has its own From/To (or Admitting into) year controls built into the
// page, so both are excluded here to avoid a second, confusing selector.
const YEAR_SCOPED_STEPS = new Set(["transport", "fees", "import", "concessions"]);

const FEES_SUBTABS = [
  { id: "transport", label: "Bus Routes", Icon: Bus },
  { id: "fees", label: "Fee Structure", Icon: ReceiptIndianRupee },
];

export default function App() {
  const [state, setState] = useState(load);
  const [signedIn, setSignedIn] = useState(false);
  const [showSetup, setShowSetup] = useState(!load());
  const [step, setStep] = useState("transport");
  // Set the moment a student is added or promoted, from Admissions, so the
  // payment window opens right there instead of sending staff off to find
  // that student again on the Fees & Concessions screen.
  const [payFor, setPayFor] = useState(null);

  useEffect(() => {
    if (!state) return;
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      /* private browsing: the session works, it just will not persist */
    }
  }, [state]);

  function handleSetup(d) {
    setState(freshWorkspace({
      name: d.name, code: d.code, address: d.address,
      adminName: d.adminName, adminEmail: d.adminEmail, password: d.password,
    }));
    setShowSetup(false);
    setSignedIn(true);
    setStep("transport");
  }

  async function handleLogin(schoolId, email, password) {
    const s = state?.school;
    if (!s || s.code !== schoolId)
      throw new Error("No school with that ID. Check it, or use Platform Setup.");
    if (s.adminEmail.toLowerCase() !== email.toLowerCase() || s.password !== password)
      throw new Error("That mail ID and password do not match.");
    setSignedIn(true);
    setStep("transport");
  }

  if (showSetup)
    return <Setup onDone={handleSetup} canGoBack={Boolean(state)} onBack={() => setShowSetup(false)} />;

  if (!signedIn)
    return <Login school={state?.school} onLogin={handleLogin} onSetupClick={() => setShowSetup(true)} />;

  function reset() {
    if (!confirm("This clears the school, routes, fee structure and students. Continue?")) return;
    localStorage.removeItem(KEY);
    setState(null);
    setSignedIn(false);
    setShowSetup(true);
  }

  const initial = (state.school.adminEmail || "A").charAt(0).toUpperCase();

  return (
    <div className="min-h-screen flex flex-col lg:flex-row">
      {/* ---------------- sidebar ---------------- */}
      <aside className="lg:w-[264px] shrink-0 bg-white border-b lg:border-b-0 lg:border-r border-slate-100 flex flex-col">
        <div className="px-6 pt-7 pb-5 text-center border-b border-slate-50">
          <div className="w-[86px] h-[86px] mx-auto rounded-2xl bg-white border border-slate-100 shadow-[0_6px_18px_-10px_rgba(15,23,41,0.3)] grid place-items-center text-[34px] leading-none">
            <span role="img" aria-hidden="true">🎓</span>
          </div>
          <p className="font-extrabold text-[15px] mt-4 leading-tight uppercase tracking-tight">
            {state.school.name}
          </p>
          <p className="eyebrow text-brand-600 mt-1.5">Fee Portal</p>
        </div>

        <nav className="flex lg:flex-col overflow-x-auto px-3 pb-3 gap-1.5">
          {NAV.map((n) => {
            // A grouped item is "on" if the current step is any of its
            // sub-steps, so Fees Setup stays highlighted on both sub-tabs.
            const on = n.group ? n.group.includes(step) : step === n.id;
            return (
              <button key={n.id}
                onClick={() => setStep(n.group ? (n.group.includes(step) ? step : n.group[0]) : n.id)}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl text-sm whitespace-nowrap transition ${
                  on ? "bg-brand-600 text-white font-bold shadow-[0_10px_22px_-12px_rgba(91,61,245,1)]"
                     : "text-slate-500 font-semibold hover:bg-slate-50"}`}>
                <n.Icon size={17} className="shrink-0" />
                {n.label}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto px-5 py-5 border-t border-slate-50 hidden lg:block">
          <p className="text-xs font-bold">{state.school.adminName}</p>
          <p className="eyebrow text-slate-400 mt-0.5">Administrator</p>
          <button onClick={() => setSignedIn(false)}
            className="mt-3 text-xs font-semibold text-slate-400 hover:text-slate-700 flex items-center gap-1.5">
            <LogOut size={13} /> Sign out
          </button>
          <button onClick={reset} className="mt-2 text-xs font-semibold text-slate-300 hover:text-red-500 block">
            Clear everything
          </button>
        </div>
      </aside>

      {/* ---------------- main ---------------- */}
      <main className="flex-1 min-w-0 px-6 lg:px-10 py-7">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-8">
          <div className="flex flex-wrap items-center gap-3">
            <div className="bg-white rounded-full pl-4 pr-5 py-2.5 border border-slate-100 shadow-[0_1px_3px_rgba(15,23,41,0.04)] flex items-center gap-2.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span className="eyebrow text-slate-400">School Environment:</span>
              <span className="eyebrow text-brand-600">{state.school.name}</span>
            </div>

            {YEAR_SCOPED_STEPS.has(step) && (
              <FilterSelect value={state.year} active
                onChange={(e) => setState({ ...state, year: e.target.value })}
                className="w-auto min-w-[110px]">
                {ACADEMIC_YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
              </FilterSelect>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button className="eyebrow text-slate-400 hover:text-slate-600 flex items-center gap-1.5">
              <RefreshCw size={13} /> Sync Now
            </button>
            <div className="bg-white rounded-full pl-1.5 pr-5 py-1.5 border border-slate-100 shadow-[0_1px_3px_rgba(15,23,41,0.04)] flex items-center gap-2.5">
              <span className="w-8 h-8 rounded-full bg-brand-600 text-white grid place-items-center font-bold text-sm">
                {initial}
              </span>
              <span className="eyebrow text-slate-500">{state.school.adminEmail}</span>
            </div>
          </div>
        </div>

        {step === "school" && <SchoolScreen state={state} save={setState} />}

        {(step === "transport" || step === "fees") && (
          <div className="mb-7 inline-flex bg-white rounded-xl border border-slate-100 p-1 shadow-[0_1px_3px_rgba(15,23,41,0.04)]">
            {FEES_SUBTABS.map((t) => {
              const on = step === t.id;
              return (
                <button key={t.id} onClick={() => setStep(t.id)}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition ${
                    on ? "bg-brand-600 text-white shadow-[0_6px_14px_-8px_rgba(91,61,245,0.9)]"
                       : "text-slate-500 hover:text-slate-700"}`}>
                  <t.Icon size={15} /> {t.label}
                </button>
              );
            })}
          </div>
        )}
        {step === "transport" && <TransportScreen state={state} save={setState} />}
        {step === "fees" && <FeeScreen state={state} save={setState} />}
        {step === "admissions" && <AdmissionScreen state={state} save={setState} onPaid={setPayFor} />}
        {step === "import" && <ImportScreen state={state} save={setState} />}
        {step === "concessions" && <ConcessionScreen state={state} save={setState} />}

        <p className="text-xs text-slate-400 mt-12 max-w-2xl leading-relaxed">
          A working prototype. Everything you enter stays in this browser — it is not
          sent anywhere and will not appear on another device.
        </p>
      </main>

      {payFor && (
        <PaymentModal state={state} save={setState} student={payFor}
          onClose={() => setPayFor(null)} />
      )}
    </div>
  );
}
