/**
 * Renderer for text/plain body parts.
 *
 * Converts plain text to HTML by escaping special characters
 * and wrapping in a paragraph with appropriate styling for whitespace preservation.
 */

/**
 * Render a text/plain body part to HTML.
 * Escapes the content using browser-native textContent for safe XSS prevention.
 */
export function renderTextPlain(content: string): string {
    if (!content) {
        return "";
    }
    const paragraph = document.createElement("p");
    paragraph.textContent = content;
    paragraph.classList.add("text-plain-content");
    return paragraph.outerHTML;
}

