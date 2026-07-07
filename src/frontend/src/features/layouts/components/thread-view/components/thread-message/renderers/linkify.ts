/**
 * Linkifier for rendered message bodies.
 *
 * Detects bare URLs living in text nodes (text that is not already wrapped
 * in a link) and turns them into anchor elements so they become clickable.
 * It must run AFTER sanitization: it only ever adds anchors whose href comes
 * from an http(s) URL matched in plain text.
 */

// A comma stops the match so that comma-separated URLs pasted without a
// space (https://a,https://b) split into distinct links instead of merging
// into one broken token.
const URL_PATTERN = /(?:https?:\/\/|www\.)[^\s<>"',]+/gi;

// Text content of those elements must never be linkified.
const SKIPPED_TAGS = new Set(["A", "STYLE", "SCRIPT", "TEXTAREA", "TITLE", "BUTTON", "SELECT", "OPTION", "NOSCRIPT", "CODE", "PRE", "SVG"]);

// Punctuation following a URL in a sentence is not part of the URL itself.
const TRAILING_PUNCTUATION = new Set([".", ";", ":", "!", "?", "'", '"', "”", "’", "»", ")", "]", "}"]);

function hasSkippedAncestor(node: Node): boolean {
    for (let element = node.parentElement; element; element = element.parentElement) {
        if (SKIPPED_TAGS.has(element.tagName)) {
            return true;
        }
    }
    return false;
}

/**
 * Strip sentence punctuation stuck to the end of a matched URL.
 * Closing parentheses are kept as long as they balance an opening
 * one inside the URL (e.g. wikipedia.org/wiki/Test_(unit)).
 */
function trimTrailingPunctuation(url: string): string {
    let end = url.length;
    while (end > 0) {
        const char = url[end - 1];
        if (!TRAILING_PUNCTUATION.has(char)) {
            break;
        }
        if (char === ")") {
            const candidate = url.slice(0, end);
            const openCount = (candidate.match(/\(/g) ?? []).length;
            const closeCount = (candidate.match(/\)/g) ?? []).length;
            if (closeCount <= openCount) {
                break;
            }
        }
        end -= 1;
    }
    return url.slice(0, end);
}

function createAnchor(doc: Document, url: string): HTMLAnchorElement {
    const anchor = doc.createElement("a");
    anchor.setAttribute("href", /^https?:\/\//i.test(url) ? url : `https://${url}`);
    anchor.setAttribute("target", "_blank");
    anchor.setAttribute("rel", "noopener noreferrer");
    anchor.textContent = url;
    return anchor;
}

/**
 * Split a text node content into a fragment mixing plain text and anchors.
 * Returns null when the text contains no URL, so the caller can keep the
 * original node untouched.
 */
function linkifyTextNode(doc: Document, text: string): DocumentFragment | null {
    const matches = Array.from(text.matchAll(URL_PATTERN));
    if (matches.length === 0) {
        return null;
    }

    const fragment = doc.createDocumentFragment();
    let cursor = 0;

    matches.forEach((match) => {
        const url = trimTrailingPunctuation(match[0]);
        if (!url) {
            return;
        }
        if (match.index > cursor) {
            fragment.appendChild(doc.createTextNode(text.slice(cursor, match.index)));
        }
        fragment.appendChild(createAnchor(doc, url));
        cursor = match.index + url.length;
    });

    if (cursor === 0) {
        return null;
    }
    if (cursor < text.length) {
        fragment.appendChild(doc.createTextNode(text.slice(cursor)));
    }
    return fragment;
}

/**
 * Turn bare URLs found in text nodes of an HTML string into clickable links.
 * URLs already wrapped in an anchor are left untouched.
 */
export function linkifyHtml(html: string): string {
    if (!html) {
        return html;
    }

    const doc = new DOMParser().parseFromString(html, "text/html");
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const textNodes: Text[] = [];
    while (walker.nextNode()) {
        const node = walker.currentNode as Text;
        if (!hasSkippedAncestor(node)) {
            textNodes.push(node);
        }
    }

    let hasChanged = false;
    textNodes.forEach((node) => {
        const fragment = linkifyTextNode(doc, node.data);
        if (fragment) {
            node.replaceWith(fragment);
            hasChanged = true;
        }
    });

    // Avoid a needless re-serialization drift when nothing matched.
    return hasChanged ? doc.body.innerHTML : html;
}
