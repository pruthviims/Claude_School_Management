import React, { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bus,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  FileSpreadsheet,
  IndianRupee,
  Percent,
  Plus,
  Trash2,
  Upload,
  Users,
} from "lucide-react";
import {
  CLASSES,
  CONCESSION_REASONS,
  IMPORT_FIELDS,
  SAMPLE_MESSY_CSV,
  TEMPLATE_CSV,
  TERMS,
  TRANSPORT_ID,
  allStops,
  computeFee,
  displayDate,
  fareRange,
  inr,
  oneTimeTotal,
  parseCSV,
  recurringTotal,
  splitHeader,
  suggestColumnMap,
  validateRows,
} from "./lib";

/* ---------------- shared bits ---------------- */

export const panel = "bg-white rounded-2xl border border-slate-100 shadow-[0_1px_3px_rgba(15,23,41,0.04)]";
const eyebrow = "eyebrow text-slate-400";
const field =
  "w-full bg-white border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm font-medium outline-none focus:border-brand-500 transition";
const cellInput =
  "w-full bg-transparent border border-transparent hover:border-slate-200 focus:border-brand-500 focus:bg-white rounded-lg px-2 py-1.5 text-sm font-medium outline-none transition";
const primary =
  "bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white text-sm font-bold rounded-xl px-5 py-2.5 flex items-center gap-2 shadow-[0_8px_20px_-8px_rgba(91,61,245,0.8)] transition";
const ghost =
  "bg-white border border-slate-200 hover:border-brand-500 hover:text-brand-600 text-slate-600 text-sm font-semibold rounded-xl px-4 py-2.5 flex items-center gap-2 transition";
const th = "text-left eyebrow text-slate-400 px-5 py-3 border-b border-slate-100";

export function PageHead({ title, subtitle, children }) {
  return (
    <div className="flex flex-wrap justify-between items-start gap-4 mb-6">
      <div>
        <h1 className="text-[30px] font-extrabold tracking-tight leading-tight">{title}</h1>
        <p className="text-slate-500 mt-1 max-w-2xl">{subtitle}</p>
      </div>
      <div className="flex flex-wrap gap-2.5">{children}</div>
    </div>
  );
}

export function StatCard({ icon: Icon, tint, label, value, note, noteTint }) {
  return (
    <div className={`${panel} p-6`}>
      <div className="flex items-center gap-3 mb-4">
        <div className={`w-11 h-11 rounded-xl grid place-items-center ${tint}`}>
          <Icon size={19} />
        </div>
        <span className="eyebrow text-slate-400">{label}</span>
      </div>
      <p className="text-[34px] font-extrabold tracking-tight leading-none tabular-nums">{value}</p>
      <p className={`eyebrow mt-2 ${noteTint || "text-slate-400"}`}>{note}</p>
    </div>
  );
}

function uid() {
  return `id${Math.random().toString(36).slice(2, 9)}`;
}

/* ================================================================== */
/* School and admin                                                    */
/* ================================================================== */

export function SchoolScreen({ state, save }) {
  const [form, setForm] = useState({ ...state.school });
  const [saved, setSaved] = useState(false);
  const set = (k) => (e) => { setForm({ ...form, [k]: e.target.value }); setSaved(false); };

  return (
    <div>
      <PageHead title="School Profile"
        subtitle="The details printed on every bill and receipt, and the account that manages them." />

      <div className={`${panel} p-6 grid sm:grid-cols-2 gap-5 max-w-3xl`}>
        <div className="sm:col-span-2">
          <label className={eyebrow}>School name</label>
          <input className={`${field} mt-2`} value={form.name} onChange={set("name")} />
        </div>
        <div>
          <label className={eyebrow}>School ID</label>
          <input className={`${field} mt-2 bg-slate-50 text-slate-400`} value={form.code} disabled />
        </div>
        <div>
          <label className={eyebrow}>Academic year</label>
          <select className={`${field} mt-2`} value={state.year}
            onChange={(e) => save({ ...state, year: e.target.value })}>
            {["2025-26", "2026-27", "2027-28"].map((y) => <option key={y}>{y}</option>)}
          </select>
        </div>
        <div className="sm:col-span-2">
          <label className={eyebrow}>Address</label>
          <textarea rows={2} className={`${field} mt-2`} value={form.address} onChange={set("address")} />
        </div>
        <div>
          <label className={eyebrow}>Administrator</label>
          <input className={`${field} mt-2`} value={form.adminName} onChange={set("adminName")} />
        </div>
        <div>
          <label className={eyebrow}>Admin mail ID</label>
          <input className={`${field} mt-2`} value={form.adminEmail} onChange={set("adminEmail")} />
        </div>
        <div className="sm:col-span-2 flex items-center gap-3 pt-1">
          <button className={primary}
            onClick={() => { save({ ...state, school: { ...state.school, ...form } }); setSaved(true); }}>
            Save changes
          </button>
          {saved && (
            <span className="text-sm font-semibold text-emerald-600 flex items-center gap-1">
              <Check size={15} /> Saved
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/* Transport                                                           */
/* ================================================================== */

export function TransportScreen({ state, save }) {
  const [openId, setOpenId] = useState(state.routes[0]?.id || null);
  const routes = state.routes;
  const setRoutes = (next) => save({ ...state, routes: next });
  const patch = (id, changes) => setRoutes(routes.map((r) => (r.id === id ? { ...r, ...changes } : r)));

  function addRoute() {
    const r = { id: uid(), code: `R-${String(routes.length + 1).padStart(2, "0")}`,
      name: "", vehicleNo: "", driverName: "", driverPhone: "", seats: 40, stops: [] };
    setRoutes([...routes, r]);
    setOpenId(r.id);
  }

  const riders = (stopId) => state.students.filter((s) => s.stopId === stopId).length;
  const stops = allStops(routes);
  const range = fareRange(routes);
  const totalRiders = state.students.filter((s) => s.stopId).length;

  function patchStop(routeId, stopId, changes) {
    const route = routes.find((r) => r.id === routeId);
    patch(routeId, { stops: route.stops.map((s) => (s.id === stopId ? { ...s, ...changes } : s)) });
  }

  function removeStop(routeId, stopId) {
    if (riders(stopId) > 0 &&
        !confirm("Students board at this stop. Removing it clears their transport fee. Continue?")) return;
    save({
      ...state,
      routes: routes.map((r) =>
        r.id === routeId ? { ...r, stops: r.stops.filter((s) => s.id !== stopId) } : r),
      students: state.students.map((s) => (s.stopId === stopId ? { ...s, stopId: null } : s)),
    });
  }

  return (
    <div>
      <PageHead title="Bus Routes"
        subtitle="Transport is a fee component, but its amount depends on where the child boards — so it is priced per stop, not per class.">
        <button className={primary} onClick={addRoute}><Plus size={16} /> Add Route</button>
      </PageHead>

      <div className="grid sm:grid-cols-3 gap-5 mb-6">
        <StatCard icon={Bus} tint="bg-brand-50 text-brand-600" label="Routes"
          value={routes.length} note="In service" />
        <StatCard icon={Users} tint="bg-emerald-50 text-emerald-600" label="Riders"
          value={totalRiders} note={`Across ${stops.length} stops`} noteTint="text-emerald-600" />
        <StatCard icon={IndianRupee} tint="bg-amber-50 text-amber-600" label="Fare range"
          value={range ? `${inr(range.min)}–${inr(range.max)}` : "—"} note="Yearly, per stop"
          noteTint="text-amber-600" />
      </div>

      {routes.length === 0 ? (
        <div className={`${panel} border-dashed p-12 text-center`}>
          <Bus className="mx-auto text-slate-300 mb-3" size={30} />
          <p className="font-bold text-slate-700">No routes yet</p>
          <p className="text-sm text-slate-500 mt-1 mb-5">
            Add a route, then list its stops with a yearly fare for each.
          </p>
          <button className={`${primary} mx-auto`} onClick={addRoute}><Plus size={16} /> Add Route</button>
        </div>
      ) : (
        <div className="space-y-3">
          {routes.map((r) => {
            const open = openId === r.id;
            const fares = r.stops.map((s) => s.fare || 0).filter(Boolean);
            return (
              <div key={r.id} className={`${panel} overflow-hidden ${open ? "border-brand-200" : ""}`}>
                <button className="w-full flex items-center gap-3 px-5 py-4 hover:bg-slate-50/70 text-left"
                  onClick={() => setOpenId(open ? null : r.id)}>
                  {open ? <ChevronDown size={17} className="text-slate-300" />
                        : <ChevronRight size={17} className="text-slate-300" />}
                  <span className="w-10 h-10 rounded-xl bg-brand-50 text-brand-600 grid place-items-center font-bold text-xs tabular-nums">
                    {r.code.replace("R-", "")}
                  </span>
                  <span className="font-bold flex-1">{r.name || "Untitled route"}</span>
                  <span className="text-xs font-semibold text-slate-400">
                    {r.stops.length} {r.stops.length === 1 ? "stop" : "stops"}
                    {fares.length > 0 && ` · ${inr(Math.min(...fares))}–${inr(Math.max(...fares))}`}
                  </span>
                </button>

                {open && (
                  <div className="border-t border-slate-100 p-5">
                    <div className="grid sm:grid-cols-3 gap-4 mb-5">
                      {[["Route code", "code", "R-01"], ["Route name", "name", "Kanakapura Road"],
                        ["Vehicle number", "vehicleNo", "KA 01 AB 1234"], ["Driver", "driverName", ""],
                        ["Driver phone", "driverPhone", ""], ["Seats", "seats", "40"]].map(([lbl, key, ph]) => (
                        <div key={key}>
                          <label className={eyebrow}>{lbl}</label>
                          <input className={`${field} mt-2`} value={r[key]} placeholder={ph}
                            onChange={(e) => patch(r.id, {
                              [key]: key === "seats" ? +e.target.value || 0 : e.target.value })} />
                        </div>
                      ))}
                    </div>

                    <div className="rounded-xl border border-slate-100 overflow-hidden">
                      <table className="w-full">
                        <thead className="bg-slate-50/70">
                          <tr>
                            <th className={th} style={{ width: "40%" }}>Stop</th>
                            <th className={th}>Pickup</th>
                            <th className={`${th} text-right`}>Yearly fare</th>
                            <th className={`${th} text-right`}>Riders</th>
                            <th className={th} />
                          </tr>
                        </thead>
                        <tbody>
                          {r.stops.map((s) => (
                            <tr key={s.id} className="border-b border-slate-50 last:border-0">
                              <td className="px-4 py-1.5">
                                <input className={cellInput} value={s.name} placeholder="Jayanagar 4th Block"
                                  onChange={(e) => patchStop(r.id, s.id, { name: e.target.value })} />
                              </td>
                              <td className="px-4 py-1.5">
                                <input className={cellInput} value={s.time} placeholder="7:20 am"
                                  onChange={(e) => patchStop(r.id, s.id, { time: e.target.value })} />
                              </td>
                              <td className="px-4 py-1.5">
                                <input className={`${cellInput} text-right tabular-nums`} inputMode="numeric"
                                  value={s.fare || ""} placeholder="0"
                                  onChange={(e) => patchStop(r.id, s.id, { fare: +e.target.value || 0 })} />
                              </td>
                              <td className="px-4 py-1.5 text-right text-sm font-semibold text-slate-400 tabular-nums">
                                {riders(s.id) || "—"}
                              </td>
                              <td className="px-4 py-1.5 text-right">
                                <button className="text-slate-300 hover:text-red-500"
                                  onClick={() => removeStop(r.id, s.id)} aria-label="Remove stop">
                                  <Trash2 size={15} />
                                </button>
                              </td>
                            </tr>
                          ))}
                          {r.stops.length === 0 && (
                            <tr><td colSpan={5} className="px-4 py-5 text-sm text-slate-400">No stops yet.</td></tr>
                          )}
                        </tbody>
                      </table>
                    </div>

                    <div className="flex gap-2 mt-4">
                      <button className={ghost} onClick={() => patch(r.id, {
                        stops: [...r.stops, { id: uid(), name: "", fare: 0, time: "" }] })}>
                        <Plus size={15} /> Add stop
                      </button>
                      <button className="text-sm font-semibold text-red-500 hover:bg-red-50 border border-slate-200 hover:border-red-200 rounded-xl px-4 py-2.5 flex items-center gap-2"
                        onClick={() => setRoutes(routes.filter((x) => x.id !== r.id))}>
                        <Trash2 size={15} /> Delete route
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */
/* Fee structure                                                       */
/* ================================================================== */

export function FeeScreen({ state, save }) {
  const [active, setActive] = useState(CLASSES[10].name);
  const [copyOpen, setCopyOpen] = useState(false);
  const rows = state.structure[active] || [];
  const range = fareRange(state.routes);

  const update = (next) => save({ ...state, structure: { ...state.structure, [active]: next } });
  const patchRow = (id, changes) => update(rows.map((r) => (r.id === id ? { ...r, ...changes } : r)));
  const hasTransport = rows.some((r) => r.id === TRANSPORT_ID);

  function toggleTransport() {
    if (hasTransport) update(rows.filter((r) => r.id !== TRANSPORT_ID));
    else update([...rows, { id: TRANSPORT_ID, name: "Transport fee", terms: [0, 0, 0], oneTime: false }]);
  }

  function copyTo(targets) {
    const next = { ...state.structure };
    for (const t of targets)
      next[t] = rows.map((r) => (r.id === TRANSPORT_ID ? { ...r } : { ...r, id: uid() }));
    save({ ...state, structure: next });
    setCopyOpen(false);
  }

  return (
    <div>
      <PageHead title="Fee Structure"
        subtitle={`What each class is charged for ${state.year}, split across three terms. These amounts are copied onto a student when they enrol.`}>
        <button className={ghost} onClick={() => setCopyOpen(!copyOpen)}>Copy to other classes</button>
      </PageHead>

      {copyOpen && <CopyPanel active={active} onCopy={copyTo} onCancel={() => setCopyOpen(false)} />}

      <div className="grid lg:grid-cols-[200px_1fr] gap-5 items-start">
        <nav className={`${panel} p-2 max-h-[70vh] overflow-y-auto`}>
          {CLASSES.map((c) => {
            const list = state.structure[c.name] || [];
            const on = c.name === active;
            return (
              <button key={c.name} onClick={() => setActive(c.name)}
                className={`w-full flex justify-between items-center gap-2 px-3 py-2 rounded-xl text-left text-sm transition ${
                  on ? "bg-brand-600 text-white font-bold shadow-[0_6px_16px_-8px_rgba(91,61,245,0.9)]"
                     : "text-slate-600 font-semibold hover:bg-slate-50"}`}>
                <span>{c.name}</span>
                <span className={`text-[11px] tabular-nums ${on ? "text-brand-100" : "text-slate-400"}`}>
                  {inr(recurringTotal(list))}
                </span>
              </button>
            );
          })}
        </nav>

        <div className={`${panel} min-w-0 overflow-hidden`}>
          <div className="px-6 py-5 border-b border-slate-100">
            <h2 className="text-lg font-extrabold">{active}</h2>
            <p className="text-sm text-slate-500 mt-0.5">
              {CLASSES.find((c) => c.name === active).stage} ·{" "}
              <b className="text-slate-700 tabular-nums">{inr(recurringTotal(rows))}</b> a year
              {oneTimeTotal(rows) > 0 && (
                <> · <span className="tabular-nums">{inr(oneTimeTotal(rows))}</span> once, on first admission</>
              )}
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[660px]">
              <thead className="bg-slate-50/70">
                <tr>
                  <th className={th}>Component</th>
                  {TERMS.map((t) => <th key={t} className={`${th} text-right`}>Term {t}</th>)}
                  <th className={`${th} text-right`}>Year</th>
                  <th className={th}>Charged</th>
                  <th className={th} />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  if (r.id === TRANSPORT_ID) {
                    return (
                      <tr key={r.id} className="border-b border-slate-50 bg-amber-50/40">
                        <td className="px-5 py-3">
                          <span className="text-sm font-bold flex items-center gap-2">
                            <Bus size={15} className="text-amber-500" /> {r.name}
                          </span>
                        </td>
                        <td colSpan={3} className="px-5 py-3 text-sm font-semibold text-amber-700">
                          Set per bus stop, not per class
                        </td>
                        <td className="px-5 py-3 text-right text-sm font-bold tabular-nums text-amber-700">
                          {range ? `${inr(range.min)}–${inr(range.max)}` : "no fares set"}
                        </td>
                        <td className="px-5 py-3 text-xs font-semibold text-slate-400">Riders only</td>
                        <td className="px-5 py-3 text-right">
                          <button className="text-slate-300 hover:text-red-500" onClick={toggleTransport}
                            aria-label="Remove transport component"><Trash2 size={15} /></button>
                        </td>
                      </tr>
                    );
                  }
                  const total = r.terms.reduce((a, b) => a + (b || 0), 0);
                  return (
                    <tr key={r.id} className="border-b border-slate-50">
                      <td className="px-5 py-1.5">
                        <input className={cellInput} value={r.name} placeholder="Name this component"
                          onChange={(e) => patchRow(r.id, { name: e.target.value })} />
                      </td>
                      {TERMS.map((t, i) => (
                        <td key={t} className="px-5 py-1.5">
                          <input className={`${cellInput} text-right tabular-nums`} inputMode="numeric"
                            value={r.terms[i] || ""} placeholder="0"
                            onChange={(e) => patchRow(r.id, {
                              terms: r.terms.map((x, j) =>
                                j === i ? Math.max(0, Math.round(+e.target.value || 0)) : x) })} />
                        </td>
                      ))}
                      <td className="px-5 py-1.5 text-right text-sm font-bold tabular-nums">{inr(total)}</td>
                      <td className="px-5 py-1.5">
                        <button onClick={() => patchRow(r.id, { oneTime: !r.oneTime })}
                          className={`text-[11px] font-bold rounded-lg px-2.5 py-1 border whitespace-nowrap ${
                            r.oneTime ? "border-brand-200 bg-brand-50 text-brand-600"
                                      : "border-slate-200 text-slate-400 hover:border-slate-300"}`}>
                          {r.oneTime ? "New admissions" : "Every year"}
                        </button>
                      </td>
                      <td className="px-5 py-1.5 text-right">
                        <button className="text-slate-300 hover:text-red-500"
                          onClick={() => update(rows.filter((x) => x.id !== r.id))}
                          aria-label={`Remove ${r.name}`}><Trash2 size={15} /></button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap gap-2 px-5 py-4 border-t border-slate-100">
            <button className={ghost}
              onClick={() => update([...rows, { id: uid(), name: "", terms: [0, 0, 0], oneTime: false }])}>
              <Plus size={15} /> Add component
            </button>
            {!hasTransport && (
              <button className={ghost} onClick={toggleTransport}>
                <Bus size={15} /> Add transport
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CopyPanel({ active, onCopy, onCancel }) {
  const [picked, setPicked] = useState([]);
  return (
    <div className={`${panel} p-5 mb-5`}>
      <p className="text-sm font-semibold text-slate-600 mb-3">
        Replace the structure of these classes with {active}'s:
      </p>
      <div className="flex flex-wrap gap-2 mb-4">
        {CLASSES.filter((c) => c.name !== active).map((c) => (
          <button key={c.name}
            onClick={() => setPicked(picked.includes(c.name)
              ? picked.filter((x) => x !== c.name) : [...picked, c.name])}
            className={`text-xs font-bold rounded-lg px-3 py-1.5 border ${
              picked.includes(c.name) ? "bg-brand-600 border-brand-600 text-white"
                                      : "border-slate-200 text-slate-500 hover:border-slate-300"}`}>
            {c.name}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <button className={primary} disabled={!picked.length} onClick={() => onCopy(picked)}>
          Copy to {picked.length || "no"} {picked.length === 1 ? "class" : "classes"}
        </button>
        <button className={ghost} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/* ================================================================== */
/* Import students — class chosen from a dropdown, no separate picker  */
/* ================================================================== */

export function ImportScreen({ state, save }) {
  // Default to the first class with students already in it, else the first
  // class on the ladder. Either way the dropdown is never empty on load.
  const [klass, setKlass] = useState(
    () => state.students[0]?.className || CLASSES[0].name,
  );
  return <ClassImport state={state} save={save} klass={klass} setKlass={setKlass} />;
}

function ClassImport({ state, save, klass, setKlass }) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [map, setMap] = useState(null);
  const [headers, setHeaders] = useState([]);
  const [body, setBody] = useState([]);
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);
  const [filter, setFilter] = useState("all");
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);

  const rows = useMemo(() => {
    if (!map || !body.length) return [];
    return validateRows(body, map, {
      existingAdmissionNos: state.students.map((s) => s.admissionNo),
      routes: state.routes,
      defaultClass: klass,
    });
  }, [map, body, state.students, state.routes, klass]);

  const good = rows.filter((r) => !r.errors.length);
  const bad = rows.filter((r) => r.errors.length);
  const warned = good.filter((r) => r.warnings.length);

  function ingest(content, name) {
    setError(""); setDone(null);
    try {
      const { headers: h, body: b } = splitHeader(parseCSV(content));
      if (!b.length) return setError("That file has a header row but no students under it.");
      setHeaders(h); setBody(b); setMap(suggestColumnMap(h)); setFilename(name);
    } catch {
      setError("That file could not be read. Save it as CSV and try again.");
    }
  }

  function readFile(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => ingest(String(reader.result), file.name);
    reader.onerror = () => setError("The file could not be read.");
    reader.readAsText(file);
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    readFile(e.dataTransfer.files?.[0]);
  }

  function commit() {
    const added = good.map((r) => ({
      id: uid(), admissionNo: r.admissionNo, name: r.fullName,
      className: r.className, section: r.section, rollNo: r.rollNo, dob: r.dob,
      guardianName: r.guardianName, phone: r.phone, email: r.email, stopId: r.stopId,
      admissionType: "continuing",
      concession: r.concession || { type: "percent", value: 0, reason: "", includeTransport: false },
    }));
    save({ ...state, students: [...state.students, ...added] });
    setDone({ created: added.length, skipped: bad.length });
    setMap(null); setBody([]); setHeaders([]); setText("");
  }

  function download(content, name) {
    const url = URL.createObjectURL(new Blob([content], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
  }

  const shown = filter === "errors" ? bad : filter === "warnings" ? warned
    : filter === "ok" ? good : rows;
  const existing = state.students.filter((s) => s.className === klass);
  const total = state.students.length;
  const countOf = (name) => state.students.filter((s) => s.className === name).length;

  // Switching class mid-upload would silently reassign whatever is on
  // screen against the wrong roll, so the picker resets the file instead.
  function changeClass(next) {
    setKlass(next);
    setMap(null); setBody([]); setHeaders([]); setText(""); setError(""); setDone(null);
  }

  return (
    <div>
      <PageHead title="Student Records"
        subtitle="Import your existing roll one class at a time. Choose the class below, then upload the sheet the office already keeps." />

      <div className="grid sm:grid-cols-3 gap-5 mb-6">
        <StatCard icon={Users} tint="bg-brand-50 text-brand-600" label="Students on roll"
          value={total} note="Across all classes" />
        <StatCard icon={FileSpreadsheet} tint="bg-emerald-50 text-emerald-600" label="Classes filled"
          value={CLASSES.filter((c) => countOf(c.name) > 0).length}
          note={`of ${CLASSES.length}`} noteTint="text-emerald-600" />
        <StatCard icon={Bus} tint="bg-amber-50 text-amber-600" label="On transport"
          value={state.students.filter((s) => s.stopId).length} note="Assigned a stop"
          noteTint="text-amber-600" />
      </div>

      <div className={`${panel} p-5 mb-6 flex flex-wrap items-end gap-4`}>
        <div className="min-w-[240px]">
          <label className={eyebrow}>Importing into</label>
          <select className={`${field} mt-2 font-bold`} value={klass}
            onChange={(e) => changeClass(e.target.value)}>
            {CLASSES.map((c) => {
              const n = countOf(c.name);
              return (
                <option key={c.name} value={c.name}>
                  {c.name} — {c.stage}{n ? ` (${n} already in)` : ""}
                </option>
              );
            })}
          </select>
        </div>
        <p className="text-xs text-slate-500 pb-2.5 max-w-md">
          Every row in the file below goes into <b className="text-slate-700">{klass}</b>{" "}
          unless the sheet names a different class for that row.
        </p>
      </div>

      {done && (
        <div className="mb-6 bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-2xl px-5 py-4 text-sm font-semibold">
          Imported {done.created} students into {klass}.{" "}
          {done.skipped > 0
            ? `${done.skipped} rows were skipped. Fix them in your sheet and upload again — anything already in is caught as a duplicate.`
            : "Every row came through cleanly."}
        </div>
      )}

      {!map && (
        <>
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            className={`${panel} border-2 border-dashed p-12 text-center cursor-pointer transition ${
              dragging ? "border-brand-500 bg-brand-50" : "border-slate-200 hover:border-brand-300"}`}>
            <div className="w-14 h-14 rounded-2xl bg-brand-50 text-brand-600 grid place-items-center mx-auto mb-4">
              <Upload size={24} />
            </div>
            <p className="text-lg font-extrabold">Drop your CSV file here</p>
            <p className="text-sm text-slate-500 mt-1">or click to browse your computer</p>
            <span className="inline-flex items-center gap-2 mt-5 bg-brand-600 text-white text-sm font-bold rounded-xl px-5 py-2.5 shadow-[0_8px_20px_-8px_rgba(91,61,245,0.8)]">
              <FileSpreadsheet size={16} /> Choose CSV file
            </span>
            <p className="text-xs text-slate-400 mt-5">
              From Excel or Google Sheets: File → Download → Comma-separated values (.csv)
            </p>
            <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden"
              onChange={(e) => { readFile(e.target.files?.[0]); e.target.value = ""; }} />
          </div>

          <div className="flex flex-wrap gap-2.5 mt-4">
            <button className={ghost} onClick={() => download(TEMPLATE_CSV, `${klass}-import-template.csv`)}>
              <Download size={15} /> Blank template
            </button>
            <button className={ghost} onClick={() => { setText(SAMPLE_MESSY_CSV); }}>
              Load a messy example
            </button>
          </div>

          <details className="mt-5">
            <summary className="eyebrow text-slate-400 cursor-pointer hover:text-slate-600">
              Or paste rows instead
            </summary>
            <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
              className={`${field} font-mono text-xs mt-3`}
              placeholder={"Adm No,Name,Section\n2026/0001,Ananya K,A"} />
            <button className={`${primary} mt-3`} disabled={!text.trim()}
              onClick={() => ingest(text, "pasted rows")}>Read these rows</button>
          </details>

          {existing.length > 0 && (
            <div className={`${panel} mt-6 overflow-hidden`}>
              <div className="px-6 py-4 border-b border-slate-100">
                <h2 className="font-extrabold">{existing.length} already in {klass}</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-slate-50/70">
                    <tr>{["Admission no.", "Name", "Section", "Guardian", "Phone"].map((h) =>
                      <th key={h} className={th}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {existing.slice(0, 25).map((s) => (
                      <tr key={s.id} className="border-b border-slate-50 text-sm font-medium">
                        <td className="px-5 py-2.5 tabular-nums text-slate-500">{s.admissionNo}</td>
                        <td className="px-5 py-2.5 font-semibold">{s.name}</td>
                        <td className="px-5 py-2.5">{s.section}</td>
                        <td className="px-5 py-2.5">{s.guardianName || "—"}</td>
                        <td className="px-5 py-2.5 tabular-nums">{s.phone || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {error && (
        <div className="my-5 flex items-start gap-2 bg-amber-50 border border-amber-200 text-amber-800 rounded-2xl px-5 py-4 text-sm font-semibold">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {map && (
        <>
          <div className={`${panel} p-5 mb-5`}>
            <h2 className="font-extrabold mb-1">Check the columns from {filename}</h2>
            <p className="text-sm text-slate-500 mb-4">
              Your headings were matched to the fields below. Class is optional here —
              anything blank goes into {klass}.
            </p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {IMPORT_FIELDS.map((f) => (
                <div key={f.key}>
                  <label className={eyebrow}>
                    {f.label}
                    {f.required && <span className="text-red-500"> required</span>}
                  </label>
                  <select className={`${field} mt-2`} value={map[f.key] ?? ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      const next = { ...map };
                      if (v === "") delete next[f.key]; else next[f.key] = +v;
                      setMap(next);
                    }}>
                    <option value="">Not in my file</option>
                    {headers.map((h, i) => <option key={i} value={i}>{h || `Column ${i + 1}`}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </div>

          <div className="grid sm:grid-cols-4 gap-4 mb-5">
            {[["all", rows.length, "Rows read", "text-ink", "bg-slate-100 text-slate-500"],
              ["ok", good.length, "Ready to import", "text-emerald-600", "bg-emerald-50 text-emerald-600"],
              ["warnings", warned.length, "To check", "text-amber-600", "bg-amber-50 text-amber-600"],
              ["errors", bad.length, "Cannot import", "text-red-500", "bg-red-50 text-red-500"],
            ].map(([key, count, lbl, colour, tint]) => (
              <button key={key} onClick={() => setFilter(key)}
                className={`${panel} p-5 text-left transition ${
                  filter === key ? "border-brand-500 ring-1 ring-brand-500" : "hover:border-slate-200"}`}>
                <div className={`w-9 h-9 rounded-xl grid place-items-center mb-3 ${tint}`}>
                  {key === "errors" ? <AlertTriangle size={16} /> : key === "ok" ? <Check size={16} />
                    : key === "warnings" ? <AlertTriangle size={16} /> : <FileSpreadsheet size={16} />}
                </div>
                <p className={`text-[26px] font-extrabold tabular-nums leading-none ${colour}`}>{count}</p>
                <p className="eyebrow text-slate-400 mt-1.5">{lbl}</p>
              </button>
            ))}
          </div>

          <div className={`${panel} overflow-x-auto`}>
            <table className="w-full min-w-[900px]">
              <thead className="bg-slate-50/70">
                <tr>{["Line", "Admission no.", "Name", "Class", "Sec", "Date of birth",
                      "Guardian", "Phone", "Bus stop", "What we found"].map((h) =>
                  <th key={h} className={th}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.lineNo} className={`border-b border-slate-50 text-sm font-medium ${
                    r.errors.length ? "bg-red-50/60" : r.warnings.length ? "bg-amber-50/50" : ""}`}>
                    <td className="px-5 py-2.5 text-slate-300 tabular-nums">{r.lineNo}</td>
                    <td className="px-5 py-2.5 tabular-nums">{r.admissionNo || <em className="text-slate-300">blank</em>}</td>
                    <td className="px-5 py-2.5 font-semibold">{r.fullName || <em className="text-slate-300 font-normal">blank</em>}</td>
                    <td className="px-5 py-2.5">
                      {r.className || <em className="text-slate-300">{r.rawClass || "blank"}</em>}
                      {r.className && r.rawClass && r.className !== r.rawClass && (
                        <span className="text-[11px] text-slate-400 ml-1.5">was “{r.rawClass}”</span>
                      )}
                    </td>
                    <td className="px-5 py-2.5">{r.section}</td>
                    <td className="px-5 py-2.5">{displayDate(r.dob) || <span className="text-slate-300">—</span>}</td>
                    <td className="px-5 py-2.5">{r.guardianName || <span className="text-slate-300">—</span>}</td>
                    <td className="px-5 py-2.5 tabular-nums">{r.phone || <span className="text-slate-300">—</span>}</td>
                    <td className="px-5 py-2.5">
                      {r.stopName || (r.rawStop ? <em className="text-slate-300">{r.rawStop}</em>
                        : <span className="text-slate-300">—</span>)}
                    </td>
                    <td className="px-5 py-2.5 max-w-xs">
                      {r.errors.map((m, i) => <div key={i} className="text-xs font-semibold text-red-600">{m}</div>)}
                      {r.warnings.map((m, i) => <div key={i} className="text-xs font-semibold text-amber-600">{m}</div>)}
                      {!r.errors.length && !r.warnings.length &&
                        <span className="text-xs text-slate-300">Looks fine</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className={`${panel} p-5 mt-5 flex flex-wrap justify-between items-center gap-4`}>
            <div>
              <p className="font-extrabold">
                Import {good.length} into {klass}
                {bad.length > 0 && <span className="font-semibold text-slate-400"> · {bad.length} skipped</span>}
              </p>
              <p className="text-xs text-slate-500 mt-1 max-w-xl">
                Imported students are recorded as continuing, not new admissions, so none
                is charged an admission fee.
              </p>
            </div>
            <div className="flex gap-2.5">
              <button className={primary} disabled={!good.length} onClick={commit}>
                <Upload size={16} /> Import {good.length}
              </button>
              <button className={ghost} onClick={() => { setMap(null); setBody([]); setError(""); }}>
                Start over
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ================================================================== */
/* Concessions                                                         */
/* ================================================================== */

export function ConcessionScreen({ state, save }) {
  const [query, setQuery] = useState("");
  const [onlyWith, setOnlyWith] = useState(false);
  const [classFilter, setClassFilter] = useState("");
  const [sectionFilter, setSectionFilter] = useState("");
  const stops = allStops(state.routes);

  const patchStudent = (id, changes) =>
    save({ ...state, students: state.students.map((s) => (s.id === id ? { ...s, ...changes } : s)) });
  const setConcession = (s, changes) =>
    patchStudent(s.id, { concession: { ...s.concession, ...changes } });

  // Sections are free text from import, not a fixed list, so they are
  // derived from whoever is actually on the roll for the chosen class —
  // narrower once a class is picked, so the list never shows a section
  // that doesn't exist in that class.
  const sectionsForClass = classFilter
    ? [...new Set(
        state.students.filter((s) => s.className === classFilter).map((s) => s.section),
      )].sort()
    : [];

  function changeClass(next) {
    setClassFilter(next);
    setSectionFilter("");
  }

  const shown = state.students.filter((s) => {
    if (classFilter && s.className !== classFilter) return false;
    if (sectionFilter && s.section !== sectionFilter) return false;
    if (onlyWith && !(s.concession?.value > 0)) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.admissionNo.toLowerCase().includes(q);
  });

  // Totals reflect the same filters as the table, so the numbers above
  // always describe what's actually listed below.
  const totals = shown.reduce((a, s) => {
    const f = computeFee(s, state);
    return { gross: a.gross + f.gross, concession: a.concession + f.concession,
             net: a.net + f.net, count: a.count + (f.concession > 0 ? 1 : 0) };
  }, { gross: 0, concession: 0, net: 0, count: 0 });

  if (!state.students.length) {
    return (
      <div>
        <PageHead title="Fees & Concessions"
          subtitle="Once students are imported, this is where each one's discount is set." />
        <div className={`${panel} border-dashed p-12 text-center text-slate-400 font-semibold`}>
          No students yet. Import your roll first.
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHead title="Fees & Concessions"
        subtitle="Each fee is the class structure plus transport for the student's stop, less any concession. Zero is a perfectly normal value." />

      <div className="grid sm:grid-cols-4 gap-5 mb-6">
        <StatCard icon={Users} tint="bg-brand-50 text-brand-600" label="Students"
          value={shown.length}
          note={shown.length === state.students.length ? "On the roll" : `Of ${state.students.length} on the roll`} />
        <StatCard icon={IndianRupee} tint="bg-slate-100 text-slate-500" label="Gross fees"
          value={inr(totals.gross)} note="Before concessions" />
        <StatCard icon={Percent} tint="bg-amber-50 text-amber-600" label="Concessions"
          value={inr(totals.concession)} note={`${totals.count} students`} noteTint="text-amber-600" />
        <StatCard icon={Check} tint="bg-emerald-50 text-emerald-600" label="Net payable"
          value={inr(totals.net)} note="Billable this year" noteTint="text-emerald-600" />
      </div>

      <div className="flex flex-wrap gap-2.5 mb-5">
        <select className={`${field} w-auto min-w-[160px] font-semibold`} value={classFilter}
          onChange={(e) => changeClass(e.target.value)}>
          <option value="">All classes</option>
          {CLASSES.map((c) => (
            <option key={c.name} value={c.name}>{c.name}</option>
          ))}
        </select>
        <select className={`${field} w-auto min-w-[140px] font-semibold disabled:bg-slate-50 disabled:text-slate-300`}
          value={sectionFilter} disabled={!classFilter}
          onChange={(e) => setSectionFilter(e.target.value)}>
          <option value="">{classFilter ? "All sections" : "Select a class first"}</option>
          {sectionsForClass.map((sec) => (
            <option key={sec} value={sec}>Section {sec}</option>
          ))}
        </select>
        <input className={`${field} max-w-xs`} value={query} placeholder="Find by name or admission no."
          onChange={(e) => setQuery(e.target.value)} />
        <button onClick={() => setOnlyWith(!onlyWith)}
          className={`text-sm font-semibold rounded-xl px-4 py-2.5 border transition ${
            onlyWith ? "bg-brand-50 border-brand-500 text-brand-600"
                     : "bg-white border-slate-200 text-slate-500 hover:border-slate-300"}`}>
          With a concession
        </button>
        {(classFilter || sectionFilter || query || onlyWith) && (
          <button onClick={() => { setClassFilter(""); setSectionFilter(""); setQuery(""); setOnlyWith(false); }}
            className="text-sm font-semibold rounded-xl px-4 py-2.5 text-slate-400 hover:text-slate-600">
            Clear filters
          </button>
        )}
      </div>

      <div className={`${panel} overflow-hidden`}>
        <div className="px-6 py-5 border-b border-slate-100">
          <h2 className="text-lg font-extrabold">
            Student Fee Records
            {(classFilter || sectionFilter) && (
              <span className="font-semibold text-slate-400 text-base">
                {" "}— {classFilter || "all classes"}{sectionFilter ? `-${sectionFilter}` : ""}
              </span>
            )}
          </h2>
        </div>
        {shown.length === 0 ? (
          <p className="px-6 py-10 text-center text-slate-400 font-semibold">
            No students match {classFilter ? `${classFilter}${sectionFilter ? `-${sectionFilter}` : ""}` : "these filters"}.
          </p>
        ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px]">
            <thead className="bg-slate-50/70">
              <tr>
                <th className={th}>Student info</th>
                <th className={th}>Class</th>
                <th className={th}>Bus stop</th>
                <th className={`${th} text-right`}>Transport</th>
                <th className={`${th} text-right`}>Gross fee</th>
                <th className={th}>Concession</th>
                <th className={th}>Reason</th>
                <th className={`${th} text-right`}>Discount</th>
                <th className={`${th} text-right`}>Net payable</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => {
                const fee = computeFee(s, state);
                const c = s.concession || {};
                return (
                  <tr key={s.id} className="border-b border-slate-50 text-sm font-medium">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-3">
                        <span className="w-9 h-9 rounded-full bg-brand-50 text-brand-600 grid place-items-center font-bold text-xs shrink-0">
                          {s.name.charAt(0).toUpperCase()}
                        </span>
                        <span>
                          <span className="block font-bold">{s.name}</span>
                          <span className="block eyebrow text-slate-400">ID: {s.admissionNo}</span>
                        </span>
                      </div>
                    </td>
                    <td className="px-5 py-3 whitespace-nowrap font-semibold">{s.className}-{s.section}</td>
                    <td className="px-5 py-3">
                      <select className={`${cellInput} border-slate-100`} value={s.stopId || ""}
                        onChange={(e) => patchStudent(s.id, { stopId: e.target.value || null })}>
                        <option value="">No bus</option>
                        {stops.map((st) => (
                          <option key={st.id} value={st.id}>{st.routeCode} · {st.name}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-5 py-3 text-right tabular-nums text-slate-500">
                      {fee.transport ? inr(fee.transport) : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="px-5 py-3 text-right tabular-nums font-semibold">{inr(fee.gross)}</td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => setConcession(s, { type: c.type === "percent" ? "amount" : "percent" })}
                          className="w-8 h-8 shrink-0 rounded-lg border border-slate-200 text-slate-500 text-xs font-bold hover:border-brand-500 hover:text-brand-600"
                          title="Switch between a percentage and a flat amount">
                          {c.type === "amount" ? "₹" : "%"}
                        </button>
                        <input inputMode="numeric" value={c.value || ""} placeholder="0"
                          className="w-20 border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm font-semibold text-right tabular-nums outline-none focus:border-brand-500"
                          onChange={(e) => {
                            let v = Math.max(0, +e.target.value || 0);
                            if (c.type !== "amount") v = Math.min(100, v);
                            setConcession(s, { value: v });
                          }} />
                      </div>
                    </td>
                    <td className="px-5 py-3">
                      <select className={`${cellInput} border-slate-100`} value={c.reason || ""}
                        disabled={!(c.value > 0)}
                        onChange={(e) => setConcession(s, { reason: e.target.value })}>
                        <option value="">—</option>
                        {CONCESSION_REASONS.map((r) => <option key={r}>{r}</option>)}
                      </select>
                      {c.value > 0 && fee.transport > 0 && (
                        <label className="flex items-center gap-1.5 mt-1.5 text-[11px] font-semibold text-slate-400">
                          <input type="checkbox" checked={!!c.includeTransport}
                            onChange={(e) => setConcession(s, { includeTransport: e.target.checked })} />
                          also discount transport
                        </label>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right tabular-nums font-semibold text-red-500">
                      {fee.concession ? `−${inr(fee.concession)}` : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <span className="inline-block bg-emerald-50 text-emerald-700 font-bold tabular-nums rounded-lg px-3 py-1.5">
                        {inr(fee.net)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
      </div>

      <p className="text-xs text-slate-400 mt-5 max-w-3xl leading-relaxed">
        A percentage applies only to the components the student is actually charged.
        Transport is excluded by default, because it is money the school passes to the
        bus operator rather than its own income — tick the box on a row to include it.
        A flat amount is capped at the fee, so a concession can take a bill to zero but
        never below it.
      </p>
    </div>
  );
}
