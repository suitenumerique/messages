/**
 * Body part renderers for converting JMAP-style body parts to HTML.
 *
 * With the JMAP algorithm, both textBody and htmlBody arrays can contain
 * multiple parts of different types (text/plain, text/html, image/*, etc.).
 * These renderers convert each part type to HTML for display.
 */

import { renderTextHtml, ExternalImageOptions } from "./text_html";
import { renderTextPlain } from "./text_plain";
import { renderImage } from "./image";

/** Represents a body part from the JMAP-style API response. */
export interface BodyPart {
    partId: string;
    type: string;
    content: string;
    cid?: string;
}

/**
 * Render a single body part to HTML based on its MIME type.
 */
function renderBodyPart(
    part: BodyPart,
    cidToBlobUrlMap: Map<string, string>,
    externalImageOptions?: ExternalImageOptions
): string {
    if (part.type === "text/html") {
        return renderTextHtml(part.content, cidToBlobUrlMap, externalImageOptions);
    }
    if (part.type === "text/plain") {
        return renderTextPlain(part.content);
    }
    if (part.type.startsWith("image/")) {
        return renderImage(part.type, part.content, part.cid, cidToBlobUrlMap);
    }
    // Unknown type - skip
    return "";
}

/**
 * Render all body parts to a single HTML string.
 * Multiple parts are wrapped in divs with CSS styling for separation.
 */
export function renderBodyParts(
    parts: readonly BodyPart[],
    cidToBlobUrlMap: Map<string, string>,
    externalImageOptions?: ExternalImageOptions
): string {
    const rendered = parts
        .map((part) => renderBodyPart(part, cidToBlobUrlMap, externalImageOptions))
        .filter(Boolean);

    if (rendered.length === 0) {
        return "";
    }

    if (rendered.length === 1) {
        return rendered[0];
    }

    // Multiple parts - wrap each in a div for CSS-based separation
    return rendered.map(html => `<div class="body-part">${html}</div>`).join("");
}
