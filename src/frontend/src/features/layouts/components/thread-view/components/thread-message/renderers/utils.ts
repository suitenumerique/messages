// Helper to parse a dimension string (with or without units) and return pixels as a number.
export const parseDimension = (dimension: string = ''): number => {
    const [, value, unit] = dimension.trim().match(/(^[\d\.]+)(.*)/) ?? [dimension, dimension, null];
    const size = parseFloat(value);
    if (isNaN(size)) return Infinity;

    switch (unit) {
        case "":
        case "px":
            // Treat plain number as pixels
            return size;
        case "em":
        case "rem":
            // em or rem: make a guess (commonly 16px = 1em)
            return size * 16;
        default:
            return Infinity;
    }
};

// Extract the first candidate URL of a srcset attribute. A URL may contain commas,
// so as in the HTML spec, the URL ends at the first whitespace and only its
// trailing commas are candidate separators.
export const getFirstSrcsetUrl = (srcset: string): string | null => {
    const url = srcset.replace(/^[\s,]+/, "").match(/^\S+/)?.[0].replace(/,+$/, "");
    return url || null;
};

// A length relative to the viewport height: vh, vb, vmin, vmax, their small,
// large and dynamic variants (svh, lvh, dvh...), and the container query units,
// which fall back to the viewport without a size container. The number must
// follow a separator or a sign, so that a name like `slide-100vh` is kept.
const VIEWPORT_HEIGHT_LENGTH = /(?:^|[\s(,/*+])[-+]?\d*\.?\d+(?:[sld]?v|cq)(?:h|b|min|max)(?![\w-])/i;
// Any viewport height unit after a digit, whatever its context: a superset of
// VIEWPORT_HEIGHT_LENGTH, cheap enough to test the whole message HTML.
const VIEWPORT_HEIGHT_UNIT_HINT = /\d(?:[sld]?v|cq)(?:h|b|min|max)/i;
// URLs and strings may hold text looking like a length. The browser serializes
// them between double quotes, escaping the quotes they contain.
const URLS_AND_STRINGS = /url\("(?:[^"\\]|\\.)*"\)|url\([^)]*\)|"(?:[^"\\]|\\.)*"/gi;

const removeViewportHeightDeclarations = (style: CSSStyleDeclaration) => {
    // Copy the property names first: removing one shifts the live list.
    Array.from(style).forEach((property) => {
        const value = style.getPropertyValue(property).replace(URLS_AND_STRINGS, "");
        if (VIEWPORT_HEIGHT_LENGTH.test(value)) {
            style.removeProperty(property);
        }
    });
};

const removeViewportHeightRules = (rules: CSSRuleList) => {
    Array.from(rules).forEach((rule) => {
        // Rules may come from another realm (an iframe), where instanceof
        // checks against this window's CSSOM classes fail.
        const { style, cssRules } = rule as Partial<CSSStyleRule>;
        if (style) removeViewportHeightDeclarations(style);
        if (cssRules) removeViewportHeightRules(cssRules);
    });
};

/**
 * Tell whether a message may hold lengths relative to the viewport height, by
 * testing its source text. It lets {@link removeViewportHeightLengths} walk the
 * style sheets of the few messages using such lengths only.
 *
 * @param html - The sanitized HTML of the message, which holds all of its CSS
 * @returns false when the message surely has no such length
 */
export const mayHaveViewportHeightLengths = (html: string): boolean =>
    VIEWPORT_HEIGHT_UNIT_HINT.test(html);

/**
 * Drop the declarations sized against the viewport height from a rendered
 * message document. A message shown in a thread has no viewport of its own:
 * its iframe takes the height of its content, so no iframe height suits a
 * `100vh` element and the message would overflow its iframe. The CSS is
 * already parsed by the browser, only the declaration values are read.
 *
 * @param doc - The loaded document of the message iframe, edited in place
 * @returns Nothing: the style sheets and inline styles of `doc` are updated
 */
export const removeViewportHeightLengths = (doc: Document): void => {
    Array.from(doc.styleSheets).forEach((sheet) => removeViewportHeightRules(sheet.cssRules));
    doc.querySelectorAll("[style]").forEach((element) => {
        // Older browsers give MathML elements no inline style object.
        const { style } = element as Partial<ElementCSSInlineStyle>;
        if (style) removeViewportHeightDeclarations(style);
    });
};
