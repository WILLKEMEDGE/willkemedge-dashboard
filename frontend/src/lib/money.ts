/**
 * Shared money formatting.
 *
 * `formatBalance` is the one place a rent-roll balance becomes text. The
 * balance can legitimately go negative — a tenant in credit — and every page
 * that shows it (tenant list, tenant detail, building drill-down, dashboard,
 * reports) is expected to render that the same way: "X cr" rather than a bare
 * "-X", which reads as an error rather than money in hand. Duplicating this
 * per-page is how one page quietly drifts from the rest.
 */

/**
 * Whole shillings. `toLocaleString()` with no options keeps up to three
 * decimal places, which is how the dashboard came to print "KES 1,275,919.8"
 * expected: commercial rent is grossed up by 16% VAT server-side, so a
 * portfolio total lands on a fraction of a shilling. Cents are not something
 * this business quotes — rent, arrears and collection are all whole figures —
 * so they are rounded away here rather than in each caller.
 */
const WHOLE_SHILLINGS: Intl.NumberFormatOptions = { maximumFractionDigits: 0 };

/** "KES 20,000" — a plain money amount with a currency prefix. */
export function formatKES(value: string | number | null | undefined): string {
  return `KES ${Number(value || 0).toLocaleString(undefined, WHOLE_SHILLINGS)}`;
}

/**
 * "20,000" in arrears (owed), "20,000 cr" in credit, "0" when square.
 * No currency prefix — callers that want one wrap the result themselves.
 */
export function formatBalance(value: string | number | null | undefined): string {
  const amount = Number(value || 0);
  if (amount < 0) {
    return `${Math.abs(amount).toLocaleString(undefined, WHOLE_SHILLINGS)} cr`;
  }
  return amount.toLocaleString(undefined, WHOLE_SHILLINGS);
}

/** Same as `formatBalance` but with the "KES" prefix, for summary/KPI cards. */
export function formatBalanceKES(value: string | number | null | undefined): string {
  return `KES ${formatBalance(value)}`;
}

/** Tailwind tone class: "owed" when the balance is positive, "clear" when square or in credit. */
export function balanceTone(value: string | number | null | undefined): "owed" | "clear" {
  return Number(value || 0) > 0 ? "owed" : "clear";
}
