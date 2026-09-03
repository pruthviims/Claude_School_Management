import React, { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  KeyRound,
  Loader2,
  Lock,
  Mail,
  ShieldCheck,
  User as UserIcon,
} from "lucide-react";

export const DEMO_SETUP_TOKEN = "school-setup-2026";

const shell =
  "min-h-screen flex items-center justify-center p-4 bg-gradient-to-b from-white via-slate-50 to-[#eef0f8]";
const cardCls =
  "w-full max-w-md bg-white rounded-3xl px-9 py-10 shadow-[0_20px_60px_-20px_rgba(15,23,41,0.18)]";
const fieldLabel = "eyebrow text-slate-400";
const fieldWrap = "relative mt-2";
const fieldIcon = "absolute left-4 top-1/2 -translate-y-1/2 text-slate-400";
const fieldInput =
  "w-full bg-slate-100/80 rounded-xl border border-transparent pl-11 pr-4 py-3.5 text-[15px] font-semibold text-ink placeholder:text-slate-400 placeholder:font-semibold outline-none focus:bg-white focus:border-brand-500 transition";
const bigButton =
  "w-full mt-7 bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white text-[15px] font-bold rounded-xl py-4 flex items-center justify-center gap-2 shadow-[0_10px_25px_-8px_rgba(91,61,245,0.7)] transition";

function ErrorNote({ children }) {
  return (
    <div className="mb-5 text-xs font-semibold bg-red-50 border border-red-200 text-red-600 rounded-xl px-4 py-3">
      {children}
    </div>
  );
}

function Crest({ emoji }) {
  return (
    <div className="w-[104px] h-[104px] rounded-[28px] bg-white border border-slate-100 shadow-[0_8px_24px_-10px_rgba(15,23,41,0.25)] grid place-items-center text-[46px] leading-none">
      <span role="img" aria-hidden="true">{emoji}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function Login({ school, onLogin, onSetupClick }) {
  const [schoolId, setSchoolId] = useState(school?.code || "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await onLogin(schoolId.trim().toLowerCase(), email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={shell}>
      <div className={cardCls}>
        <div className="flex flex-col items-center mb-8">
          <Crest emoji="🎓" />
          <h1 className="text-[30px] font-extrabold tracking-tight mt-5">School Portal</h1>
          <p className="text-sm text-slate-500 mt-1">Secure Fee Administration Gateway</p>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}

        <form onSubmit={submit}>
          <div className="mb-5">
            <label className={fieldLabel}>School ID</label>
            <div className={fieldWrap}>
              <Building2 className={fieldIcon} size={17} />
              <input required value={schoolId} onChange={(e) => setSchoolId(e.target.value)}
                placeholder="e.g. school-id" className={fieldInput} />
            </div>
          </div>

          <div className="mb-5">
            <label className={fieldLabel}>Admin Username</label>
            <div className={fieldWrap}>
              <UserIcon className={fieldIcon} size={17} />
              <input required value={email} onChange={(e) => setEmail(e.target.value)}
                placeholder="Mail ID" className={fieldInput} />
            </div>
          </div>

          <div>
            <div className="flex items-baseline justify-between">
              <label className={fieldLabel}>Password</label>
              <button type="button" className="eyebrow text-brand-600 hover:text-brand-700"
                onClick={() => setError("Password recovery is not wired up in this prototype yet.")}>
                Forgot?
              </button>
            </div>
            <div className={fieldWrap}>
              <Lock className={fieldIcon} size={17} />
              <input required type="password" value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••" className={fieldInput} />
            </div>
          </div>

          <button type="submit" disabled={busy} className={bigButton}>
            {busy ? <Loader2 className="animate-spin" size={17} /> : null}
            Enter Portal
            {!busy && <ArrowRight size={17} />}
          </button>
        </form>

        <div className="border-t border-slate-100 mt-8 pt-6 text-center">
          <button onClick={onSetupClick} className="eyebrow text-brand-600 hover:text-brand-700">
            Platform Setup
          </button>
          <p className="eyebrow text-emerald-600 flex items-center justify-center gap-1.5 mt-4">
            <ShieldCheck size={13} /> AES-256 cloud encryption active
          </p>
        </div>

        {school && (
          <p className="mt-5 text-[11px] text-center text-slate-400 leading-relaxed">
            Prototype sign-in — School ID <b className="text-slate-500">{school.code}</b>,
            e-mail <b className="text-slate-500">{school.adminEmail}</b>, and the password
            you set during setup.
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function Setup({ onDone, onBack, canGoBack }) {
  const [f, setF] = useState({
    name: "", code: "", address: "", adminName: "",
    email: "", password: "", confirm: "", token: "",
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  function submit(e) {
    e.preventDefault();
    setError(null);
    if (!/^[a-z0-9-_]{3,}$/.test(f.code.trim().toLowerCase()))
      return setError("School ID needs at least 3 characters: lowercase letters, numbers, hyphen or underscore.");
    if (f.password.length < 8) return setError("Password must be at least 8 characters.");
    if (f.password !== f.confirm) return setError("The two passwords do not match.");
    if (f.token.trim() !== DEMO_SETUP_TOKEN)
      return setError("That setup token is not valid. Ask whoever runs the server for it.");

    setBusy(true);
    onDone({
      name: f.name.trim(), code: f.code.trim().toLowerCase(), address: f.address.trim(),
      adminName: f.adminName.trim(), adminEmail: f.email.trim(), password: f.password,
    });
  }

  const row = (lbl, key, Icon, props = {}) => (
    <div className="mb-4">
      <label className={fieldLabel}>{lbl}</label>
      <div className={fieldWrap}>
        <Icon className={fieldIcon} size={17} />
        <input value={f[key]} onChange={set(key)} className={fieldInput} {...props} />
      </div>
    </div>
  );

  return (
    <div className={shell}>
      <div className={cardCls}>
        {canGoBack && (
          <button onClick={onBack}
            className="flex items-center gap-1.5 eyebrow text-slate-400 hover:text-slate-600 mb-5">
            <ArrowLeft size={13} /> Back to login
          </button>
        )}

        <div className="flex flex-col items-center mb-8">
          <Crest emoji="🏫" />
          <h1 className="text-[26px] font-extrabold tracking-tight mt-5">Platform Setup</h1>
          <p className="text-sm text-slate-500 mt-1 text-center">
            Creates the school and its first administrator.
          </p>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}

        <form onSubmit={submit}>
          {row("School Name", "name", Building2, { required: true, placeholder: "Vidya Mandir Public School" })}
          {row("School ID", "code", KeyRound, { required: true, placeholder: "vidya-mandir" })}
          {row("Address", "address", Building2, { placeholder: "48, 4th Cross, Jayanagar" })}
          {row("Administrator Name", "adminName", UserIcon, { required: true, placeholder: "R. Krishnamurthy" })}
          {row("Admin Mail ID", "email", Mail, { required: true, type: "email", placeholder: "principal@school.edu.in" })}
          {row("Password", "password", Lock, { required: true, type: "password", placeholder: "••••••••" })}
          {row("Confirm Password", "confirm", Lock, { required: true, type: "password", placeholder: "••••••••" })}

          <div>
            <label className={fieldLabel}>Setup Token</label>
            <div className={fieldWrap}>
              <ShieldCheck className={fieldIcon} size={17} />
              <input required type="password" value={f.token} onChange={set("token")}
                placeholder="Provided by your system administrator" className={fieldInput} />
            </div>
            <p className="text-[11px] text-slate-400 mt-2 leading-relaxed">
              Checked against the server's <code>ADMIN_SETUP_TOKEN</code> in the real
              build, where it never reaches the browser. This prototype has no server,
              so use <b className="text-slate-600">{DEMO_SETUP_TOKEN}</b>.
            </p>
          </div>

          <button type="submit" disabled={busy} className={bigButton}>
            {busy ? <Loader2 className="animate-spin" size={17} /> : null}
            Create School Workspace
            {!busy && <ArrowRight size={17} />}
          </button>
        </form>
      </div>
    </div>
  );
}
