const SVG_NS = "http://www.w3.org/2000/svg";
const BADGE_COLOR = "#D7010E"; // --c--globals--colors--error-550
const BADGE_MASK_ID = "favicon-unread-badge-cutout";

type FaviconLink = { el: HTMLLinkElement; baseHref: string };

let links: FaviconLink[] = [];
let badgeEnabled = false;
const badgedHrefs = new Map<string, Promise<string>>();

type ViewBox = { minX: number; minY: number; width: number; height: number };

/** Keep the generated coordinates readable: 1/100 of a viewBox unit is far
 * below what a 16px favicon can resolve. */
const round = (value: number) => String(Math.round(value * 100) / 100);

const readViewBox = (svg: Element): ViewBox => {
  const values = (svg.getAttribute("viewBox") ?? "")
    .split(/[\s,]+/)
    .map(Number)
    .filter((value) => Number.isFinite(value));
  if (values.length !== 4 || values[2] <= 0 || values[3] <= 0) {
    throw new Error("Favicon SVG has no usable viewBox");
  }
  return { minX: values[0], minY: values[1], width: values[2], height: values[3] };
};

/**
 * Add an unread dot to a theme favicon: the glyph is punched out around the
 * dot so it stays readable at 16px, where a flat overlay would blend into the
 * artwork below it.
 */
const buildBadgedSvg = (source: string): string => {
  const doc = new DOMParser().parseFromString(source, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg.tagName !== "svg" || doc.querySelector("parsererror")) {
    throw new Error("Favicon source is not a valid SVG");
  }

  const { minX, minY, width, height } = readViewBox(svg);
  const radius = 9;
  const cx = minX + width - radius * 1.4;
  const cy = minY + height - radius * 1.4;

  const mask = doc.createElementNS(SVG_NS, "mask");
  mask.setAttribute("id", BADGE_MASK_ID);
  mask.setAttribute("maskUnits", "userSpaceOnUse");
  mask.setAttribute("x", round(minX));
  mask.setAttribute("y", round(minY));
  mask.setAttribute("width", round(width));
  mask.setAttribute("height", round(height));

  const maskFill = doc.createElementNS(SVG_NS, "rect");
  maskFill.setAttribute("x", round(minX));
  maskFill.setAttribute("y", round(minY));
  maskFill.setAttribute("width", round(width));
  maskFill.setAttribute("height", round(height));
  maskFill.setAttribute("fill", "white");

  const maskHole = doc.createElementNS(SVG_NS, "circle");
  maskHole.setAttribute("cx", round(cx));
  maskHole.setAttribute("cy", round(cy));
  maskHole.setAttribute("r", round(radius * 1.5));
  maskHole.setAttribute("fill", "black");

  mask.append(maskFill, maskHole);

  const masked = doc.createElementNS(SVG_NS, "g");
  masked.setAttribute("mask", `url(#${BADGE_MASK_ID})`);
  masked.append(...Array.from(svg.childNodes));

  const badge = doc.createElementNS(SVG_NS, "circle");
  badge.setAttribute("cx", round(cx));
  badge.setAttribute("cy", round(cy));
  badge.setAttribute("r", round(radius));
  badge.setAttribute("fill", BADGE_COLOR);

  svg.append(mask, masked, badge);

  return new XMLSerializer().serializeToString(svg);
};

const getBadgedHref = (baseHref: string): Promise<string> => {
  let pending = badgedHrefs.get(baseHref);
  if (!pending) {
    pending = fetch(baseHref)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((source) => `data:image/svg+xml,${encodeURIComponent(buildBadgedSvg(source))}`)
      .catch((error) => {
        // Keep the plain favicon rather than blanking the tab icon, and drop
        // the entry so the next toggle retries instead of caching the failure.
        badgedHrefs.delete(baseHref);
        console.error("[favicon] Failed to build the unread badge.", error);
        return baseHref;
      });
    badgedHrefs.set(baseHref, pending);
  }
  return pending;
};

const applyBadge = (link: FaviconLink) => {
  if (!badgeEnabled) {
    link.el.href = link.baseHref;
    return;
  }
  void getBadgedHref(link.baseHref).then((href) => {
    // The badge may have been cleared, or the links reinstalled, while the
    // source SVG was in flight.
    if (badgeEnabled && link.el.isConnected) link.el.href = href;
  });
};

/**
 * Inject theme-aware SVG favicons into <head>. `index.html` only ships the
 * fixed PWA bitmap icons, which are not theme-aware. Called during bootstrap
 * so the favicon is set before the first paint.
 */
export const installThemeFavicons = (theme: string) => {
  const variants: Array<{ media: string; href: string }> = [
    { media: "(prefers-color-scheme: light)", href: `/images/${theme}/favicon-light.svg` },
    { media: "(prefers-color-scheme: dark)", href: `/images/${theme}/favicon-dark.svg` },
  ];
  links = variants.map(({ media, href }) => {
    const el = document.createElement("link");
    el.rel = "icon";
    el.type = "image/svg+xml";
    el.media = media;
    el.href = href;
    document.head.appendChild(el);
    return { el, baseHref: href };
  });
  links.forEach(applyBadge);
  return () => {
    links.forEach(({ el }) => el.remove());
    links = [];
  };
};

/**
 * Toggle the unread dot on the favicons installed by `installThemeFavicons`.
 *
 * Silently a no-op on WebKit (Safari, and every iOS browser): it reads the
 * favicon once at first paint and never re-reads it, so *no* DOM change reaches
 * the tab — not an href mutation, a link remove+append, an SVG, a data: URI, or
 * a canvas-rasterised PNG. Verified on Safari 26.5, where even the canonical
 * dynamic-favicon demo (mathiasbynens.be/demo/dynamic-favicons) stays frozen, so
 * this is the engine and not our markup. Safari 26 did add the SVG favicon
 * *format*, which is a separate thing and easy to mistake for a fix. The unread
 * signal is carried in the tab title there instead — see `unread-badge.ts`.
 * The theme favicons themselves are unaffected: `installThemeFavicons` runs
 * before the first paint, which is the one moment WebKit does read them.
 */
export const setFaviconBadge = (enabled: boolean) => {
  badgeEnabled = enabled;
  links.forEach(applyBadge);
};
