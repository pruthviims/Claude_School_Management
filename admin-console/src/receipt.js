// Receipt PDF, drawn client-side with jsPDF. Mirrors the layout and content
// rules from backend/fees/services/receipts.py: A5 sheet, Indian digit
// grouping, lakh/crore amount-in-words, a GST-exempt note (education
// services are exempt), and a DUPLICATE watermark on anything that isn't
// the original print.
//
// The PDF is built entirely from the payment record's own snapshot fields
// (feeLines, grossAtPayment, etc.) rather than from live state, so a
// reprint always matches what was actually collected — even if the fee
// structure or the student's concession has changed since.

import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import { amountInWords, displayDate, inrPlain } from "./lib";

const PAGE_W = 559.37; // A5 landscape, points
const PAGE_H = 419.53;
const MARGIN = 32;

function modeLabel(mode) {
  return { cash: "Cash", upi: "UPI", card: "Card", netbanking: "Net banking",
    cheque: "Cheque" }[mode] || mode;
}

export function buildReceiptPdf({ school, payment, duplicate = false }) {
  const doc = new jsPDF({ unit: "pt", format: "a5", orientation: "landscape" });
  const right = PAGE_W - MARGIN;

  // Outer border, echoing the printed-sheet feel of the backend template.
  doc.setDrawColor(60, 60, 70);
  doc.setLineWidth(0.75);
  doc.rect(MARGIN - 10, MARGIN - 12, PAGE_W - 2 * (MARGIN - 10), PAGE_H - 2 * (MARGIN - 12));

  if (duplicate) {
    doc.saveGraphicsState();
    doc.setTextColor(210, 60, 60);
    doc.setFontSize(46);
    doc.setFont("helvetica", "bold");
    try { doc.setGState(new doc.GState({ opacity: 0.18 })); } catch { /* older jsPDF: skip opacity */ }
    doc.text("DUPLICATE", PAGE_W / 2, PAGE_H / 2, { align: "center", angle: 22 });
    doc.restoreGraphicsState();
  }

  // Header: school on the left, document identity on the right.
  doc.setTextColor(20, 20, 25);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(15);
  doc.text(school.name || "School", MARGIN, MARGIN + 6);

  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(90, 90, 100);
  const addrLines = doc.splitTextToSize(school.address || "", 260);
  doc.text(addrLines, MARGIN, MARGIN + 20);

  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.setTextColor(20, 20, 25);
  doc.text("FEE RECEIPT", right, MARGIN + 4, { align: "right" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.text(`No. ${payment.receiptNo}`, right, MARGIN + 18, { align: "right" });
  doc.text(`Date: ${displayDate(payment.receivedOn)}`, right, MARGIN + 30, { align: "right" });

  doc.setDrawColor(30, 30, 35);
  doc.setLineWidth(1.1);
  doc.line(MARGIN, MARGIN + 40, right, MARGIN + 40);

  // Student identity grid.
  const gridY = MARGIN + 58;
  const cols = [MARGIN, MARGIN + 165, MARGIN + 300, MARGIN + 400];
  const grid = [
    ["Student", payment.studentName],
    ["Admission no.", payment.admissionNo],
    ["Class", payment.classAtPayment],
    ["Academic year", payment.year],
  ];
  grid.forEach(([label, value], i) => {
    doc.setFontSize(7);
    doc.setTextColor(120, 120, 130);
    doc.text(label.toUpperCase(), cols[i], gridY);
    doc.setFontSize(9.5);
    doc.setFont("helvetica", "bold");
    doc.setTextColor(20, 20, 25);
    doc.text(String(value ?? ""), cols[i], gridY + 12);
    doc.setFont("helvetica", "normal");
  });

  // Line items.
  const rows = (payment.feeLines || []).map((l) => [l.name, inrPlain(l.amount)]);
  if (payment.concessionAtPayment > 0) {
    rows.push(["Concession applied", `-${inrPlain(payment.concessionAtPayment)}`]);
  }

  autoTable(doc, {
    startY: gridY + 24,
    margin: { left: MARGIN, right: MARGIN },
    tableWidth: "auto",
    tableLineWidth: 0,
    head: [["Particulars", "Amount (Rs.)"]],
    body: rows,
    foot: [["Amount received", inrPlain(payment.amount)]],
    theme: "plain",
    styles: { fontSize: 9, cellPadding: { top: 3, bottom: 3, left: 2, right: 2 },
      lineWidth: 0 },
    headStyles: { textColor: [120, 120, 130], fontStyle: "normal", fontSize: 7.5,
      halign: "left", lineWidth: 0 },
    columnStyles: { 1: { halign: "right" } },
    footStyles: { textColor: [20, 20, 25], fontStyle: "bold", fontSize: 11,
      lineWidth: { top: 1.1, left: 0, right: 0, bottom: 0 }, lineColor: [30, 30, 35] },
    didParseCell: (data) => {
      if (data.section === "head")
        data.cell.styles.lineWidth = { top: 0, left: 0, right: 0, bottom: 0.75 };
      if (data.section === "body")
        data.cell.styles.lineWidth = { top: 0, left: 0, right: 0, bottom: 0.4 };
    },
  });

  let y = doc.lastAutoTable.finalY + 16;

  doc.setFont("helvetica", "italic");
  doc.setFontSize(9);
  doc.setTextColor(70, 70, 80);
  const words = doc.splitTextToSize(amountInWords(payment.amount), PAGE_W - 2 * MARGIN);
  doc.text(words, MARGIN, y);
  y += words.length * 11 + 10;

  doc.setFont("helvetica", "normal");
  doc.setFontSize(8.5);
  doc.setTextColor(40, 40, 45);
  let modeLine = `Received by ${modeLabel(payment.mode)}`;
  if (payment.reference) modeLine += ` — ref. ${payment.reference}`;
  modeLine += ".";
  if (payment.mode === "cheque") modeLine += " Subject to realisation.";
  doc.text(modeLine, MARGIN, y);
  y += 12;
  if (payment.balanceAfterAtPayment > 0) {
    doc.setTextColor(160, 60, 50);
    doc.text(`Balance still due: Rs. ${inrPlain(payment.balanceAfterAtPayment)}`, MARGIN, y);
  } else {
    doc.setTextColor(30, 110, 80);
    doc.text("Fees settled in full for this academic year.", MARGIN, y);
  }
  // Footer.
  const footY = PAGE_H - MARGIN + 4;
  doc.setDrawColor(210, 210, 215);
  doc.setLineWidth(0.5);
  doc.line(MARGIN, footY - 20, right, footY - 20);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(7);
  doc.setTextColor(120, 120, 130);
  doc.text("Education services are exempt from GST. This is a computer-generated receipt.",
    MARGIN, footY - 8);
  doc.text(`Collected by ${payment.collectedBy || "—"}`, MARGIN, footY + 3);

  doc.setDrawColor(60, 60, 70);
  doc.line(right - 110, footY - 8, right, footY - 8);
  doc.setFontSize(7.5);
  doc.setTextColor(90, 90, 100);
  doc.text("Authorised signatory", right, footY + 3, { align: "right" });

  return doc;
}

export function downloadReceipt({ school, payment, duplicate = false }) {
  const doc = buildReceiptPdf({ school, payment, duplicate });
  const name = payment.receiptNo.replace(/\//g, "-") + (duplicate ? "-duplicate" : "");
  doc.save(`${name}.pdf`);
}
