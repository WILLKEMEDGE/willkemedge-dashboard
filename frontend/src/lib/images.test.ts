import { describe, expect, it } from "vitest";

import { avatarFor, propertyImage } from "./images";

/**
 * A privacy regression test.
 *
 * `avatarFor` is called with a real tenant's full name (Payments, Tenants) and
 * with a staff email address (Settings, the top bar). It used to return a
 * `https://api.dicebear.com/...?seed=<that value>` URL, which put personal data
 * in a third-party CDN's access logs on every render — in a system that is
 * otherwise careful enough to mask phone numbers in its own logs.
 *
 * If someone reintroduces a remote avatar service, this fails.
 */
describe("avatarFor", () => {
  it("never produces a network URL", () => {
    const url = avatarFor("Mercy Murunga");
    expect(url.startsWith("data:image/svg+xml")).toBe(true);
    expect(url).not.toContain("http://");
    expect(url).not.toContain("https://");
  });

  it("does not leak the seed as a readable query value", () => {
    // The initials appear (that is the point); the full name must not.
    const url = avatarFor("Mercy Murunga");
    expect(decodeURIComponent(url)).not.toContain("Murunga");
    expect(decodeURIComponent(url)).toContain(">MM<");
  });

  it("handles an email address, a single word and a blank seed", () => {
    expect(decodeURIComponent(avatarFor("owner@wilkem.test"))).toContain(">OW<");
    expect(decodeURIComponent(avatarFor("Osoro"))).toContain(">O<");
    expect(decodeURIComponent(avatarFor(""))).toContain(">?<");
  });

  it("survives a non-Latin-1 name (btoa would have thrown)", () => {
    expect(() => avatarFor("Zoë Ngũgĩ")).not.toThrow();
    expect(avatarFor("Zoë Ngũgĩ").startsWith("data:image/svg+xml")).toBe(true);
  });

  it("is deterministic — the same seed gives the same avatar", () => {
    expect(avatarFor("Peter Kimani")).toBe(avatarFor("Peter Kimani"));
  });
});

describe("propertyImage", () => {
  it("sends only a fixed photo id, never the building name", () => {
    const url = propertyImage("Wilkem Edge Apartments - Donholm");
    expect(url).toContain("images.unsplash.com");
    expect(url).not.toContain("Wilkem");
    expect(url).not.toContain("Donholm");
  });
});
