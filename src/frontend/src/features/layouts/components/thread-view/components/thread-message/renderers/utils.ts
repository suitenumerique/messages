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
