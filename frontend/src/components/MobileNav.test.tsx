import { describe, expect, it } from "vitest";

import { NAV_ITEMS, type ViewKey } from "@/lib/nav";

import { OVERFLOW_KEYS, PRIMARY_KEYS } from "./MobileNav";

/**
 * A reachability test, not a rendering one.
 *
 * `OVERFLOW_KEYS` used to be a hand-written list, and it had drifted: it named
 * five sections while NAV_ITEMS had thirteen. Reconciliation, Income, Water and
 * Accounting were in neither list, so four working pages had no route to them
 * on a phone at all — including the reconciliation queue, whose entire job is
 * to be cleared promptly when a bank credit cannot be matched to a tenant.
 *
 * Nothing caught it because nothing compared the two lists. This does.
 */
describe("mobile navigation", () => {
  it("reaches every section in NAV_ITEMS", () => {
    const reachable = new Set<ViewKey>([...PRIMARY_KEYS, ...OVERFLOW_KEYS]);
    const missing = NAV_ITEMS.map((i) => i.key).filter((key) => !reachable.has(key));

    expect(missing).toEqual([]);
  });

  it("never puts the same section in both the tab bar and the drawer", () => {
    const duplicated = OVERFLOW_KEYS.filter((key) => PRIMARY_KEYS.includes(key));
    expect(duplicated).toEqual([]);
  });

  it("keeps the bottom tab bar to four items so it stays usable at 375px", () => {
    // Four tabs plus the "More" button. A fifth makes the labels wrap on a
    // small phone, which the responsive E2E gate then fails on.
    expect(PRIMARY_KEYS).toHaveLength(4);
  });
});
