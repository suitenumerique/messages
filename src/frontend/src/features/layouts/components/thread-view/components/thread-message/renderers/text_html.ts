/**
 * Renderer for text/html body parts.
 *
 * Sanitizes HTML content using DOMPurify to prevent XSS attacks
 * while preserving safe formatting and structure.
 */

import DomPurify from "dompurify";

/** Options for handling external images. */
export interface ExternalImageOptions {
    canDisplayExternalImages: boolean;
    displayExternalImages: boolean;
    selectedMailboxId?: string;
    onExternalImageDetected: () => void;
    getProxiedUrl: (url: string) => string;
}

/**
 * Render HTML content with sanitization and CID resolution.
 * Opens external links in new tabs and transforms cid: references to blob URLs.
 * Handles external images according to provided options.
 */
export function renderTextHtml(
    content: string,
    cidToBlobUrlMap: Map<string, string>,
    externalImageOptions?: ExternalImageOptions
): string {
    if (!content) {
        return "";
    }

    const domPurify = DomPurify();
    const MIN_IMAGE_SIZE = 4;

    domPurify.addHook("afterSanitizeAttributes", function (node) {
        // Open external links in new tabs with safe rel attributes
        if (node.tagName === "A") {
            if (!node.getAttribute("href")?.startsWith("#")) {
                node.setAttribute("target", "_blank");
            }
            node.setAttribute("rel", "noopener noreferrer");
        }

        // Handle images: pixel tracker removal, CID resolution, external image handling
        if (node.tagName === "IMG") {
            // Remove pixel trackers (very small images)
            if (node.getAttribute("width") || node.getAttribute("height")) {
                const width = parseInt(node.getAttribute("width") ?? "0");
                const height = parseInt(node.getAttribute("height") ?? "0");
                if (Math.max(width, height) < MIN_IMAGE_SIZE) {
                    node.remove();
                    return;
                }
            }

            // Add lazy loading to all images
            node.setAttribute("loading", "lazy");

            const src = node.getAttribute("src");

            // Transform CID references to blob URLs
            if (src && src.startsWith("cid:") && cidToBlobUrlMap.size > 0) {
                const cid = src.substring(4); // Remove 'cid:' prefix
                const blobUrl = cidToBlobUrlMap.get(cid);
                if (blobUrl) {
                    node.setAttribute("src", blobUrl);
                }
                return;
            }

            // Handle external images
            if (src?.startsWith("http") && externalImageOptions) {
                externalImageOptions.onExternalImageDetected();

                if (!externalImageOptions.canDisplayExternalImages || !externalImageOptions.displayExternalImages) {
                    node.remove();
                    return;
                }

                // Proxy external images
                node.setAttribute("src", externalImageOptions.getProxiedUrl(src));
            }
        }
    });

    return domPurify.sanitize(content, {
        FORBID_TAGS: ["script", "object", "iframe", "embed", "audio", "video"],
        ADD_ATTR: ["target", "rel"],
    });
}
