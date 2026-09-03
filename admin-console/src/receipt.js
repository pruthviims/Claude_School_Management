// Receipt PDF, drawn client-side with jsPDF + jspdf-autotable, following
// the same conventions as the reference payslip generator: plain new
// jsPDF() (A4 portrait, millimetres — not a hand-picked page size), every
// table given explicit column widths that sum to the content width rather
// than left to "auto" sizing, and no decorative outer border competing
// with the table for space. That combination is what the earlier A5
// version got wrong — a hand-drawn border box sized independently of the
// table caused the amount column to spill past it on some receipts.
//
// The PDF is built entirely from the payment record's own snapshot fields
// (feeLines, grossAtPayment, etc.) rather than from live state, so a
// reprint always matches what was actually collected — even if the fee
// structure or the student's concession changed afterwards.

import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import { amountInWords, displayDate, inrPlain } from "./lib";

const LEFT = 14;
const RIGHT = 196; // A4 is 210mm wide; same left/right margin as the payslip reference
const WIDTH = RIGHT - LEFT;

function modeLabel(mode) {
  return { cash: "Cash", upi: "UPI", card: "Card", netbanking: "Net banking",
    cheque: "Cheque" }[mode] || mode;
}

export function buildReceiptPdf({ school, payment, duplicate = false }) {
  const doc = new jsPDF(); // defaults: A4, portrait, millimetres

  // Header: school on the left, document identity on the right.
  doc.setTextColor(20, 20, 20);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.text(school.name || "School", LEFT, 18);

  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(100, 100, 105);
  const addrLines = doc.splitTextToSize(school.address || "", 110);
  doc.text(addrLines, LEFT, 24);

  doc.setFont("helvetica", "bold");
  doc.setFontSize(13);
  doc.setTextColor(20, 20, 20);
  doc.text("FEE RECEIPT", RIGHT, 16, { align: "right" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9.5);
  doc.text(`No. ${payment.receiptNo}`, RIGHT, 22, { align: "right" });
  doc.text(`Date: ${displayDate(payment.receivedOn)}`, RIGHT, 28, { align: "right" });

  doc.setDrawColor(30, 30, 35);
  doc.setLineWidth(0.6);
  doc.line(LEFT, 33, RIGHT, 33);

  // Student identity — its own bordered grid, same visual language as the
  // items table below, so nothing here can overflow independently either.
  autoTable(doc, {
    startY: 39,
    margin: { left: LEFT, right: 210 - RIGHT },
    theme: "grid",
    styles: { fontSize: 9, cellPadding: 2.3, lineColor: [225, 225, 230], lineWidth: 0.2,
      textColor: [20, 20, 20] },
    body: [
      ["Student", payment.studentName, "Admission No.", payment.admissionNo],
      ["Class", payment.classAtPayment, "Academic Year", payment.year],
    ],
    columnStyles: {
      0: { fontStyle: "bold", cellWidth: 30, textColor: [100, 100, 105] },
      1: { cellWidth: 61 },
      2: { fontStyle: "bold", cellWidth: 30, textColor: [100, 100, 105] },
      3: { cellWidth: 61 },
    },
  });

  // Line items — just the fee components themselves. Concession, and the
  // whole paid/balance picture, live in the summary table below instead of
  // being folded in here, so the reading order matches what was asked for:
  // fee breakdown, then total fees, then total discount, then the payment
  // ledger.
  const rows = (payment.feeLines || []).map((l) => [l.name, inrPlain(l.amount)]);

  autoTable(doc, {
    startY: doc.lastAutoTable.finalY + 6,
    margin: { left: LEFT, right: 210 - RIGHT },
    theme: "grid",
    head: [["Particulars", "Amount (Rs.)"]],
    body: rows,
    styles: { fontSize: 9.5, cellPadding: 3, lineColor: [225, 225, 230], lineWidth: 0.2 },
    headStyles: { fillColor: [245, 245, 248], textColor: [30, 30, 30], fontStyle: "bold",
      fontSize: 8.5 },
    columnStyles: {
      0: { cellWidth: WIDTH - 45 },
      1: { cellWidth: 45, halign: "right" },
    },
  });

  // Prior instalments, oldest first — the actual "for record purposes"
  // ledger the office asked for, not just a single running total that
  // would lose the trail the next time a payment gets added.
  const priorPayments = payment.priorPayments || [];
  if (priorPayments.length > 0) {
    autoTable(doc, {
      startY: doc.lastAutoTable.finalY + 6,
      margin: { left: LEFT, right: 210 - RIGHT },
      theme: "grid",
      head: [["Previous instalments", "Date", "Amount (Rs.)"]],
      body: priorPayments.map((p) => [p.receiptNo, displayDate(p.receivedOn), inrPlain(p.amount)]),
      styles: { fontSize: 8.5, cellPadding: 2.4, lineColor: [225, 225, 230], lineWidth: 0.2,
        textColor: [70, 70, 75] },
      headStyles: { fillColor: [245, 245, 248], textColor: [30, 30, 30], fontStyle: "bold",
        fontSize: 7.5 },
      columnStyles: {
        0: { cellWidth: 82 },
        1: { cellWidth: 55 },
        2: { cellWidth: 45, halign: "right" },
      },
    });
  }

  // Payment summary: total fees, total discount, net payable, then the
  // instalment picture — what was paid before, what's being paid now, and
  // what's left — exactly the sequence asked for.
  const paidBefore = priorPayments.reduce((a, p) => a + p.amount, 0);
  const summaryRows = [["Total fees", inrPlain(payment.grossAtPayment)]];
  if (payment.concessionAtPayment > 0) {
    summaryRows.push(["Total discount", `-${inrPlain(payment.concessionAtPayment)}`]);
  }
  summaryRows.push(["Net payable", inrPlain(payment.netAtPayment)]);
  if (paidBefore > 0) summaryRows.push(["Paid previously", inrPlain(paidBefore)]);
  summaryRows.push(["Paid now", inrPlain(payment.amount)]);
  if (paidBefore > 0) {
    summaryRows.push(["Total paid to date", inrPlain(paidBefore + payment.amount)]);
  }
  const balanceRowIndex = summaryRows.length;
  summaryRows.push([
    payment.balanceAfterAtPayment > 0 ? "Balance due" : "Balance",
    payment.balanceAfterAtPayment > 0 ? inrPlain(payment.balanceAfterAtPayment) : "Paid in full",
  ]);

  autoTable(doc, {
    startY: doc.lastAutoTable.finalY + 6,
    margin: { left: LEFT, right: 210 - RIGHT },
    theme: "grid",
    body: summaryRows,
    styles: { fontSize: 9.5, cellPadding: 3, lineColor: [225, 225, 230], lineWidth: 0.2,
      fontStyle: "bold", textColor: [20, 20, 20] },
    columnStyles: {
      0: { cellWidth: WIDTH - 45 },
      1: { cellWidth: 45, halign: "right" },
    },
    didParseCell: (data) => {
      if (data.row.index === balanceRowIndex) {
        data.cell.styles.fontSize = 11;
        data.cell.styles.textColor = payment.balanceAfterAtPayment > 0
          ? [180, 60, 50] : [20, 120, 85];
        data.cell.styles.fillColor = payment.balanceAfterAtPayment > 0
          ? [253, 244, 243] : [240, 250, 246];
      }
    },
  });

  let y = doc.lastAutoTable.finalY + 8;

  doc.setFont("helvetica", "italic");
  doc.setFontSize(9);
  doc.setTextColor(80, 80, 85);
  const words = doc.splitTextToSize(`This payment: ${amountInWords(payment.amount)}`, WIDTH);
  doc.text(words, LEFT, y);
  y += words.length * 5 + 6;

  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(40, 40, 45);
  let modeLine = `Received by ${modeLabel(payment.mode)}`;
  if (payment.reference) modeLine += ` — ref. ${payment.reference}`;
  modeLine += ".";
  if (payment.mode === "cheque") modeLine += " Subject to realisation.";
  doc.text(modeLine, LEFT, y);
  y += 10;

  // Footer follows the content directly rather than pinning to the
  // physical bottom of the A4 page — a receipt legitimately only fills
  // the top portion of the sheet, the same way the reference payslip's
  // footer sits just below its table rather than at a fixed page offset.
  doc.setDrawColor(220, 220, 225);
  doc.setLineWidth(0.3);
  doc.line(LEFT, y, RIGHT, y);
  y += 6;

  doc.setFont("helvetica", "normal");
  doc.setFontSize(7.5);
  doc.setTextColor(120, 120, 125);
  doc.text("Education services are exempt from GST. This is a computer-generated receipt.",
    LEFT, y);
  doc.text(`Collected by ${payment.collectedBy || "—"}`, LEFT, y + 5);

  doc.setDrawColor(60, 60, 70);
  doc.setLineWidth(0.3);
  doc.line(RIGHT - 50, y + 2, RIGHT, y + 2);
  doc.setFontSize(8);
  doc.setTextColor(90, 90, 95);
  doc.text("Authorised signatory", RIGHT, y + 7, { align: "right" });

  // Drawn last and centred on the content actually printed (roughly
  // y / 2), not on the geometric centre of the A4 page — a receipt only
  // fills the top portion of the sheet, so centring on the full page put
  // most of the watermark over blank space and left only a sliver
  // overlapping the content.
  if (duplicate) {
    doc.saveGraphicsState();
    doc.setTextColor(210, 60, 60);
    doc.setFontSize(46);
    doc.setFont("helvetica", "bold");
    try { doc.setGState(new doc.GState({ opacity: 0.16 })); } catch { /* older jsPDF: skip opacity */ }
    doc.text("DUPLICATE", 105, Math.max(60, y / 2), { align: "center", angle: 28 });
    doc.restoreGraphicsState();
  }

  return doc;
}

export function downloadReceipt({ school, payment, duplicate = false }) {
  const doc = buildReceiptPdf({ school, payment, duplicate });
  const name = payment.receiptNo.replace(/\//g, "-") + (duplicate ? "-duplicate" : "");
  doc.save(`${name}.pdf`);
}
