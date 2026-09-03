// Shared domain logic. Class normalisation, CSV handling and validation
// mirror fees/services/importer.py; fee resolution mirrors billing.py.

export const CLASSES = [
  { name: "Pre-LKG", stage: "Pre-primary" },
  { name: "LKG", stage: "Pre-primary" },
  { name: "UKG", stage: "Pre-primary" },
  { name: "I", stage: "Primary" },
  { name: "II", stage: "Primary" },
  { name: "III", stage: "Primary" },
  { name: "IV", stage: "Primary" },
  { name: "V", stage: "Primary" },
  { name: "VI", stage: "Middle" },
  { name: "VII", stage: "Middle" },
  { name: "VIII", stage: "Middle" },
  { name: "IX", stage: "Secondary" },
  { name: "X", stage: "Secondary" },
  { name: "1st PU", stage: "Pre-university" },
  { name: "2nd PU", stage: "Pre-university" },
];

export const TERMS = [1, 2, 3];

export const ACADEMIC_YEARS = ["2025-26", "2026-27", "2027-28"];

// X -> 1st PU is never automatic: many students leave for another board or
// college after X, so promotion into these classes needs an explicit,
// per-student decision rather than a bulk "promote everyone" action.
export const OPTIN_TARGETS = new Set(["1st PU"]);

// 2nd PU has nothing above it — promoting from here means completing
// school, not moving up a class.
export const TERMINAL_CLASSES = new Set(["2nd PU"]);

export function nextClassName(current) {
  const i = CLASSES.findIndex((c) => c.name === current);
  if (i === -1 || i === CLASSES.length - 1) return null;
  return CLASSES[i + 1].name;
}

export function isTerminalClass(name) {
  return TERMINAL_CLASSES.has(name);
}

export function needsOptIn(targetClass) {
  return OPTIN_TARGETS.has(targetClass);
}

// Students carry the academic year they were enrolled in. Records saved
// before this field existed have none, so treat "no year" as belonging to
// whichever year is asked about — safe because every write path now tags
// year explicitly, this only matters for old browser-stored data.
export function inYear(student, year) {
  return (student.year || year) === year;
}

/* ---------------- payments ---------------- */

export const PAYMENT_MODES = [
  { id: "cash", label: "Cash" },
  { id: "upi", label: "UPI" },
  { id: "card", label: "Card" },
  { id: "netbanking", label: "Net banking" },
  { id: "cheque", label: "Cheque" },
];

// Gapless within a year is what a real ledger needs (a Postgres row lock in
// the Django backend); a browser prototype with one user at a time can get
// away with counting existing receipts. Noted as a known simplification —
// see backend/fees/models.py DocumentCounter for the real version.
export function nextReceiptNo(state) {
  const n = state.payments.filter((p) => p.year === state.year).length + 1;
  return `RCP/${state.year}/${String(n).padStart(5, "0")}`;
}

export function paidByStudent(state, student) {
  return state.payments
    .filter((p) => p.studentId === student.id && p.year === state.year)
    .reduce((a, p) => a + p.amount, 0);
}

const WORD_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
  "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
  "sixteen", "seventeen", "eighteen", "nineteen"];
const WORD_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
  "eighty", "ninety"];

function wordsUnderHundred(n) {
  if (n < 20) return WORD_ONES[n];
  const t = Math.floor(n / 10);
  const o = n % 10;
  return WORD_TENS[t] + (o ? `-${WORD_ONES[o]}` : "");
}

function wordsUnderThousand(n) {
  const h = Math.floor(n / 100);
  const rest = n % 100;
  const parts = [];
  if (h) parts.push(`${WORD_ONES[h]} hundred`);
  if (rest) parts.push(wordsUnderHundred(rest));
  return parts.join(" ");
}

// Indian numbering (lakh, crore) — not "million". A school accountant will
// reject a receipt that says "one million" for ₹10,00,000.
export function amountInWords(rupees) {
  const n = Math.round(Math.abs(rupees));
  if (n === 0) return "Zero rupees only";

  const crore = Math.floor(n / 10000000);
  let rest = n % 10000000;
  const lakh = Math.floor(rest / 100000);
  rest %= 100000;
  const thousand = Math.floor(rest / 1000);
  rest %= 1000;

  const chunks = [];
  if (crore) chunks.push(`${wordsUnderThousand(crore)} crore`);
  if (lakh) chunks.push(`${wordsUnderThousand(lakh)} lakh`);
  if (thousand) chunks.push(`${wordsUnderThousand(thousand)} thousand`);
  if (rest) chunks.push(wordsUnderThousand(rest));

  const out = `${chunks.join(" ")} rupees only`;
  return out.charAt(0).toUpperCase() + out.slice(1);
}

// The transport row in a fee structure carries no amount of its own — the
// figure comes from whichever stop the student boards at. Marked by this id
// so the editor can render it differently and the calculator can resolve it.
export const TRANSPORT_ID = "component-transport";

export const CONCESSION_REASONS = [
  "Sibling discount",
  "Staff ward",
  "RTE quota",
  "Merit scholarship",
  "Single parent",
  "Financial hardship",
  "Other",
];

/* ---------------- money ---------------- */

// Indian digit grouping only, no sign and no currency symbol — shared by
// inr() and inrPlain() below so the two never drift apart.
function groupIndian(n) {
  let s = String(n);
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    const groups = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    s = groups.join(",") + "," + tail;
  }
  return s;
}

export function inr(rupees) {
  const neg = rupees < 0;
  const n = Math.round(Math.abs(rupees));
  return (neg ? "-₹" : "₹") + groupIndian(n);
}

// Same formatting, no ₹. jsPDF's built-in fonts (the standard 14 PDF fonts)
// use WinAnsiEncoding, which doesn't include the Rupee sign — text run
// through inr() renders as a garbled glyph in real PDF viewers (Chrome's
// viewer shows it plainly; some tools silently drop it, which is why this
// was easy to miss in testing). Anything written into a PDF must use this
// instead, with "Rs." placed as ordinary text wherever a label is needed.
export function inrPlain(rupees) {
  const neg = rupees < 0;
  const n = Math.round(Math.abs(rupees));
  return (neg ? "-" : "") + groupIndian(n);
}

/* ---------------- transport ---------------- */

export function allStops(routes) {
  return routes.flatMap((r) =>
    r.stops.map((s) => ({ ...s, routeId: r.id, routeCode: r.code, routeName: r.name })),
  );
}

export function findStop(routes, stopId) {
  return allStops(routes).find((s) => s.id === stopId) || null;
}

export function matchStopByName(routes, name) {
  if (!name) return null;
  const t = norm(name);
  return allStops(routes).find((s) => norm(s.name) === t) || null;
}

export function fareRange(routes) {
  const fares = allStops(routes).map((s) => s.fare || 0).filter(Boolean);
  if (!fares.length) return null;
  return { min: Math.min(...fares), max: Math.max(...fares) };
}

/* ---------------- fee resolution ---------------- */

const sumTerms = (c) => c.terms.reduce((a, b) => a + (b || 0), 0);

/**
 * Work out what one student owes for the year.
 *
 * Order matters and mirrors the server: build the gross from the class
 * structure, resolve transport from the student's stop, then subtract the
 * concession. A concession never applies to a component that was not
 * charged in the first place.
 */
export function computeFee(student, state) {
  const structure = state.structure[student.className] || [];
  const isNew = student.admissionType === "new";

  const lines = [];
  for (const c of structure) {
    if (c.id === TRANSPORT_ID) continue;
    if (c.oneTime && !isNew) continue;
    const amount = sumTerms(c);
    if (amount > 0) lines.push({ id: c.id, name: c.name, amount, oneTime: !!c.oneTime });
  }

  const hasTransportRow = structure.some((c) => c.id === TRANSPORT_ID);
  const stop = student.stopId ? findStop(state.routes, student.stopId) : null;
  if (hasTransportRow && stop && stop.fare > 0) {
    lines.push({
      id: TRANSPORT_ID,
      name: `Transport — ${stop.name}`,
      amount: stop.fare,
      transport: true,
    });
  }

  const gross = lines.reduce((a, l) => a + l.amount, 0);
  const transport = lines.filter((l) => l.transport).reduce((a, l) => a + l.amount, 0);

  const c = student.concession;
  let concession = 0;
  if (c && c.value > 0) {
    // Transport is a third-party cost the school passes through, so a fee
    // concession excludes it unless the school says otherwise.
    const base = c.includeTransport ? gross : gross - transport;
    concession =
      c.type === "percent"
        ? Math.round((base * c.value) / 100)
        : Math.min(c.value, base);
  }

  return {
    lines,
    gross,
    transport,
    concession,
    net: Math.max(0, gross - concession),
  };
}

export function recurringTotal(list) {
  return list
    .filter((c) => !c.oneTime && c.id !== TRANSPORT_ID)
    .reduce((a, c) => a + sumTerms(c), 0);
}

export function oneTimeTotal(list) {
  return list.filter((c) => c.oneTime).reduce((a, c) => a + sumTerms(c), 0);
}

/* ---------------- CSV ---------------- */

export function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  const delim = pickDelimiter(text);

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += ch;
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delim) {
      row.push(field); field = "";
    } else if (ch === "\n") {
      row.push(field); field = "";
      rows.push(row); row = [];
    } else if (ch !== "\r") {
      field += ch;
    }
  }
  row.push(field);
  if (row.some((c) => c.trim())) rows.push(row);
  return rows.filter((r) => r.some((c) => (c || "").trim()));
}

function pickDelimiter(text) {
  const sample = text.slice(0, 4000);
  const counts = [",", ";", "\t", "|"].map((d) => [d, sample.split(d).length]);
  counts.sort((a, b) => b[1] - a[1]);
  return counts[0][1] > 1 ? counts[0][0] : ",";
}

export function splitHeader(rows) {
  if (!rows.length) return { headers: [], body: [] };
  let best = 0;
  let bestCount = -1;
  for (let i = 0; i < Math.min(5, rows.length); i++) {
    const count = rows[i].filter((c) => (c || "").trim()).length;
    if (count > bestCount) { bestCount = count; best = i; }
  }
  return { headers: rows[best], body: rows.slice(best + 1) };
}

/* ---------------- normalisation ---------------- */

const norm = (s) =>
  (s || "").toString().trim().toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];

export function normaliseClass(raw) {
  let t = norm(raw);
  if (!t) return "";
  t = t.replace(/^(class|std|standard|grade)\s+/, "");
  t = t.replace(/\s+(class|std|standard)$/, "");

  if (["pre lkg", "prelkg", "pre kg", "prekg", "nursery", "play home",
       "playhome", "pre nursery"].includes(t)) return "Pre-LKG";
  if (["lkg", "l k g", "jr kg", "junior kg"].includes(t)) return "LKG";
  if (["ukg", "u k g", "sr kg", "senior kg"].includes(t)) return "UKG";

  if (/^(1|1st|i)\s*(pu|puc|pum)$/.test(t)) return "1st PU";
  if (/^(2|2nd|ii)\s*(pu|puc|pum)$/.test(t)) return "2nd PU";
  if (["11", "11th", "xi"].includes(t)) return "1st PU";
  if (["12", "12th", "xii"].includes(t)) return "2nd PU";

  const m = t.match(/^(\d{1,2})(st|nd|rd|th)?$/);
  if (m) {
    const n = parseInt(m[1], 10);
    if (n >= 1 && n <= 10) return ROMAN[n];
  }
  const upper = t.toUpperCase().replace(/\s/g, "");
  if (ROMAN.includes(upper)) return upper;
  return "";
}

export function normalisePhone(raw) {
  let d = (raw || "").replace(/\D/g, "");
  if (d.length > 10 && d.startsWith("91")) d = d.slice(2);
  if (d.length > 10 && d.startsWith("0")) d = d.replace(/^0+/, "");
  return d;
}

export function parseDate(raw) {
  const t = (raw || "").trim();
  if (!t) return null;

  let m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return iso(+m[1], +m[2], +m[3]);

  m = t.match(/^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$/);
  if (m) {
    let [, d, mo, y] = m.map(Number);
    if (y < 100) y += 2000;
    if (d > 12 && mo <= 12) return iso(y, mo, d);
    if (mo > 12 && d <= 12) return iso(y, d, mo);
    return iso(y, mo, d);   // ambiguous: Indian sheets are day-first
  }

  const months = ["jan", "feb", "mar", "apr", "may", "jun",
                  "jul", "aug", "sep", "oct", "nov", "dec"];
  m = t.match(/^(\d{1,2})[\s-]([a-zA-Z]{3,})[\s-](\d{4})$/);
  if (m) {
    const idx = months.indexOf(m[2].slice(0, 3).toLowerCase());
    if (idx >= 0) return iso(+m[3], idx + 1, +m[1]);
  }
  return null;
}

function iso(y, m, d) {
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return null;
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

export function displayDate(v) {
  if (!v) return "";
  const [y, m, d] = v.split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[+m - 1]} ${y}`;
}

/* ---------------- import ---------------- */

export const IMPORT_FIELDS = [
  { key: "admission_no", label: "Admission no.", required: true,
    aliases: ["admission no", "admission number", "adm no", "admno", "reg no", "registration no"] },
  { key: "full_name", label: "Student name", required: true,
    aliases: ["name", "student name", "name of student", "full name", "student"] },
  { key: "class_name", label: "Class", requiredUnlessClassPicked: true,
    aliases: ["class", "std", "standard", "grade", "class name"] },
  { key: "section", label: "Section", aliases: ["section", "sec", "div", "division"] },
  { key: "roll_no", label: "Roll no.", aliases: ["roll no", "roll", "roll number"] },
  { key: "date_of_birth", label: "Date of birth", aliases: ["dob", "date of birth", "birth date", "d o b"] },
  { key: "gender", label: "Gender", aliases: ["gender", "sex"] },
  { key: "guardian_name", label: "Guardian name",
    aliases: ["parent name", "father name", "guardian name", "father s name", "parent", "guardian"] },
  { key: "guardian_phone", label: "Phone",
    aliases: ["phone", "mobile", "contact", "phone no", "mobile no", "contact number"] },
  { key: "guardian_email", label: "Email", aliases: ["email", "email id", "e mail"] },
  { key: "address", label: "Address", aliases: ["address", "residential address"] },
  { key: "bus_stop", label: "Bus stop", aliases: ["bus stop", "stop", "transport stop", "pickup point"] },
  { key: "concession", label: "Concession", aliases: ["concession", "discount", "rebate"] },
];

export function suggestColumnMap(headers) {
  const map = {};
  const used = new Set();
  for (const f of IMPORT_FIELDS) {
    for (let i = 0; i < headers.length; i++) {
      if (used.has(i)) continue;
      const h = norm(headers[i]);
      if (h === norm(f.key) || h === norm(f.label) || f.aliases.some((a) => norm(a) === h)) {
        map[f.key] = i;
        used.add(i);
        break;
      }
    }
  }
  return map;
}

export function validateRows(body, map, { existingAdmissionNos = [], routes = [], defaultClass = null } = {}) {
  const seen = new Set();
  const taken = new Set(existingAdmissionNos);

  return body.map((row, i) => {
    const cell = (key) => {
      const idx = map[key];
      if (idx === undefined || idx >= row.length) return "";
      return (row[idx] || "").trim();
    };

    const errors = [];
    const warnings = [];

    const admissionNo = cell("admission_no");
    const fullName = cell("full_name");
    const rawClass = cell("class_name");
    // A class named in the file always wins, so a stray row from another
    // class is caught rather than silently filed under the chosen one.
    const readClass = normaliseClass(rawClass);
    const className = readClass || defaultClass || "";

    if (!admissionNo) errors.push("Admission number is blank.");
    else if (seen.has(admissionNo))
      errors.push(`Admission number ${admissionNo} appears twice in this file.`);
    else if (taken.has(admissionNo))
      errors.push(`Admission number ${admissionNo} is already in the system.`);
    else seen.add(admissionNo);

    if (!fullName) errors.push("Student name is blank.");
    else if (fullName.length < 2) errors.push(`Name "${fullName}" looks incomplete.`);

    if (!className) {
      errors.push(
        rawClass
          ? `Could not read the class "${rawClass}". Use Pre-LKG, LKG, UKG, I to X, 1st PU or 2nd PU.`
          : "No class given, and no class was chosen for this import.",
      );
    } else if (defaultClass && readClass && readClass !== defaultClass) {
      warnings.push(
        `This row says ${readClass}, but you are importing into ${defaultClass}. It will be filed under ${readClass}.`,
      );
    }

    const rawDob = cell("date_of_birth");
    const dob = parseDate(rawDob);
    if (rawDob && !dob) warnings.push(`Could not read the date "${rawDob}". Left blank.`);

    const rawPhone = cell("guardian_phone");
    const phone = normalisePhone(rawPhone);
    if (rawPhone && phone.length !== 10)
      warnings.push(`Phone "${rawPhone}" is not 10 digits. Kept as entered.`);

    if (!cell("guardian_name"))
      warnings.push("No guardian name. Fee reminders will have no contact.");

    const rawStop = cell("bus_stop");
    const stop = matchStopByName(routes, rawStop);
    if (rawStop && !stop)
      warnings.push(`Bus stop "${rawStop}" is not on any route. No transport fee will be charged.`);

    const rawConcession = cell("concession");
    let concession = null;
    if (rawConcession) {
      const pct = rawConcession.includes("%");
      const value = parseFloat(rawConcession.replace(/[^0-9.]/g, "")) || 0;
      if (value > 0)
        concession = { type: pct ? "percent" : "amount", value,
                       reason: "Other", includeTransport: false };
      else warnings.push(`Could not read the concession "${rawConcession}". Set to none.`);
    }

    return {
      lineNo: i + 1,
      admissionNo,
      fullName,
      className,
      rawClass,
      section: (cell("section") || "A").toUpperCase().slice(0, 10),
      rollNo: cell("roll_no"),
      dob,
      guardianName: cell("guardian_name"),
      phone: phone || rawPhone,
      email: cell("guardian_email"),
      address: cell("address"),
      rawStop,
      stopId: stop?.id || null,
      stopName: stop?.name || "",
      concession,
      errors,
      warnings,
    };
  });
}

export const TEMPLATE_CSV =
  "Admission No,Name,Class,Section,Roll No,DOB,Gender,Guardian Name,Phone,Email,Address,Bus Stop,Concession\n" +
  "2026/0001,Ananya Krishnamurthy,VIII,A,1,14/03/2012,F,R Krishnamurthy,9845012345,parent@example.com,\"12 MG Road, Bengaluru\",Jayanagar 4th Block,25%\n";

export const SAMPLE_MESSY_CSV =
  "Vidya Mandir Public School - Student List 2026-27,,,,,,,\n" +
  "Adm No,Name of Student,Std,Sec,D.O.B,Father's Name,Mobile No,Stop\n" +
  "2026/0001,Ananya Krishnamurthy,VIII,A,14/03/2012,R Krishnamurthy,+91 98450 12345,Jayanagar 4th Block\n" +
  "2026/0002,Rohan Reddy,8th,B,03/04/2012,S Reddy,9845012346,Banashankari\n" +
  "2026/0003,Meera Iyer,Class 8,A,2012-05-20,K Iyer,09845012347,\n" +
  "2026/0004,Kiran Shetty,1st PU,A,11/07/2009,M Shetty,9845012348,Jayanagar 4th Block\n" +
  "2026/0005,Deepa Rao,II PUC,B,22/06/2008,N Rao,9845012349,Nowhere Junction\n" +
  "2026/0006,Arjun Gowda,Pre-KG,A,09/09/2022,P Gowda,9845012350,\n" +
  "2026/0007,,X,A,15/01/2010,T Nair,9845012351,\n" +
  "2026/0001,Duplicate Child,IX,A,01/01/2011,X Person,9845012352,\n" +
  "2026/0009,Sneha Bhat,Rocket Science,A,05/05/2011,V Bhat,notaphone,\n";
