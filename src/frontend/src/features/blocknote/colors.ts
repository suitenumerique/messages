/**
 * Inline copy of COLORS_DEFAULT from @blocknote/core (not part of the public API).
 *
 * This is both the palette the editor UI can apply and the only one its
 * stylesheet renders: BlockNote styles named selectors (`[data-text-color=blue]`),
 * so any other value is stored but never displayed.
 */
export const BLOCKNOTE_COLORS: Record<string, { text: string; background: string }> = {
    gray: { text: '#9b9a97', background: '#ebeced' },
    brown: { text: '#64473a', background: '#e9e5e3' },
    red: { text: '#e03e3e', background: '#fbe4e4' },
    orange: { text: '#d9730d', background: '#f6e9d9' },
    yellow: { text: '#dfab01', background: '#fbf3db' },
    green: { text: '#4d6461', background: '#ddedea' },
    blue: { text: '#0b6e99', background: '#ddebf1' },
    purple: { text: '#6940a5', background: '#eae4f2' },
    pink: { text: '#ad1a72', background: '#f4dfeb' },
};

/**
 * True when a `textColor` / `backgroundColor` value is one the editor can
 * actually render — a palette name or the `default` sentinel.
 *
 * Anything else comes from pasted foreign HTML: BlockNote maps a raw
 * `style="color: …"` onto these props whatever the value.
 */
export const isBlockNoteColor = (value: unknown): boolean =>
    typeof value === 'string' && (value === 'default' || value in BLOCKNOTE_COLORS);

/**
 * Resolves a `textColor` / `backgroundColor` value to its CSS color.
 *
 * @param value - the stored color value
 * @param variant - which side of the palette entry to read
 * @returns the CSS color, or `undefined` when the value is `default` or
 *   outside the palette (and therefore must not reach the exported HTML)
 */
export const resolveBlockNoteColor = (
    value: unknown,
    variant: 'text' | 'background',
): string | undefined => {
    if (typeof value !== 'string' || value === 'default') return undefined;
    return BLOCKNOTE_COLORS[value]?.[variant];
};

/** Palette CSS value (lowercase 6-digit hex) → palette name, per variant. */
const PALETTE_NAMES_BY_CSS_VALUE = {
    text: new Map(Object.entries(BLOCKNOTE_COLORS).map(([name, c]) => [c.text, name])),
    background: new Map(
        Object.entries(BLOCKNOTE_COLORS).map(([name, c]) => [c.background, name]),
    ),
};

const HEX_SHORTHAND = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/;
const HEX = /^#[0-9a-f]{6}$/;
const RGB = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)\s*(?:[,/]\s*(\d*\.?\d+)\s*)?\)$/;

/**
 * Normalizes a CSS color to a lowercase 6-digit hex, so values written in
 * different notations can be compared to the palette.
 *
 * @returns the normalized hex, or `undefined` for a notation we do not compare
 *   (named colors, `hsl()`, or any translucent color — which no palette entry is)
 */
const normalizeCssColor = (value: string): string | undefined => {
    const css = value.trim().toLowerCase();

    const shorthand = HEX_SHORTHAND.exec(css);
    if (shorthand) {
        const [, r, g, b] = shorthand;
        return `#${r}${r}${g}${g}${b}${b}`;
    }
    if (HEX.test(css)) return css;

    const rgb = RGB.exec(css);
    if (!rgb) return undefined;
    const [, r, g, b, alpha] = rgb;
    if (alpha !== undefined && Number(alpha) !== 1) return undefined;
    const channels = [r, g, b].map(Number);
    if (channels.some((channel) => channel > 255)) return undefined;
    return `#${channels.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
};

/**
 * Recognizes a raw CSS color as one of the palette entries.
 *
 * Our own exported mails carry palette colors as plain CSS (`color:#0b6e99`),
 * which the clipboard hands back as an off-palette value — so pasting a reply
 * onto a Messages mail would otherwise lose its colors. Matching is done per
 * variant: a text color is only recognized among the palette's text values.
 *
 * @returns the palette name, or `null` when the color is not one of ours
 */
export const matchBlockNoteColorName = (
    value: unknown,
    variant: 'text' | 'background',
): string | null => {
    if (typeof value !== 'string') return null;
    const normalized = normalizeCssColor(value);
    if (!normalized) return null;
    return PALETTE_NAMES_BY_CSS_VALUE[variant].get(normalized) ?? null;
};
