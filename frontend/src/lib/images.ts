/**
 * Deterministic imagery. Given a seed, the same picture every time.
 */
const PROPERTY_IMAGES = [
  "photo-1570129477492-45c003edd2be", // modern apartment
  "photo-1512917774080-9991f1c4c750", // villa
  "photo-1568605114967-8130f3a36994", // townhouse
  "photo-1600585154340-be6161a56a0c", // contemporary home
  "photo-1564013799919-ab600027ffc6", // cozy interior-ext
  "photo-1600596542815-ffad4c1539a9", // brick facade
  "photo-1600607687939-ce8a6c25118c", // airy interior
  "photo-1613553474179-e1eda3ea5734", // apartment block
  "photo-1580587771525-78b9dba3b914", // glass facade
  "photo-1512917774080-9991f1c4c750", // stone villa
  "photo-1582268611958-ebfd161ef9cf", // urban apartments
  "photo-1486406146926-c627a92ad1ab", // residential row
];

function hash(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/**
 * Decorative stock photography for a property card, keyed off the building name.
 *
 * These are Unsplash stock images, not photographs of the actual properties —
 * the seed only decides which one. No identifying data is sent: the building
 * name never leaves the browser, it is hashed to an index locally and only the
 * fixed photo id appears in the URL.
 */
export function propertyImage(seed: string | number, size: "sm" | "md" | "lg" = "md") {
  const w = size === "sm" ? 400 : size === "md" ? 800 : 1400;
  const id = PROPERTY_IMAGES[hash(String(seed)) % PROPERTY_IMAGES.length];
  return `https://images.unsplash.com/${id}?auto=format&fit=crop&q=80&w=${w}`;
}

/** On-brand backgrounds, picked deterministically from the seed. */
const AVATAR_COLORS = ["#0F2A43", "#12324D", "#1E4668", "#0D9488"];

/**
 * An initials avatar, rendered locally as an inline SVG data URI.
 *
 * This used to build a `https://api.dicebear.com/...?seed=<value>` URL, and the
 * seed passed in at every call site is a REAL TENANT'S FULL NAME (Payments,
 * Tenants) or a STAFF EMAIL ADDRESS (Settings, the top bar). Every avatar
 * render therefore put personal data in the URL of a third-party CDN, where it
 * is logged — for a Kenyan property business subject to the Data Protection Act
 * 2019, and in a backend that is otherwise careful enough to mask phone numbers
 * and keep SMS bodies out of the logs.
 *
 * Two letters do not need a network request. The signature is unchanged, so
 * every `<img src={avatarFor(...)} />` call site keeps working.
 */
export function avatarFor(seed: string | number) {
  const text = String(seed).trim();
  const initials =
    text
      .split(/[\s._@-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "?";
  const background = AVATAR_COLORS[hash(text) % AVATAR_COLORS.length];

  // Rendered at a fixed 100×100 and scaled by the caller's CSS.
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">` +
    `<rect width="100" height="100" fill="${background}"/>` +
    `<text x="50" y="50" dy="0.36em" fill="#ffffff" font-size="42" ` +
    `font-family="Inter, system-ui, -apple-system, sans-serif" font-weight="600" ` +
    `text-anchor="middle">${initials}</text></svg>`;

  // encodeURIComponent, not btoa: the seed can contain non-Latin-1 characters
  // (a tenant name with an accent), which btoa throws on.
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
