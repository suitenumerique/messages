/**
 * Renderer for text/html body parts.
 *
 * Sanitizes HTML content using DOMPurify to prevent XSS attacks
 * while preserving safe formatting and structure.
 */

import DomPurify from "dompurify";
import { parseDimension } from "./utils";

/** Options for handling external images. */
export interface ExternalImageOptions {
    canDisplayExternalImages: boolean;
    displayExternalImages: boolean;
    selectedMailboxId?: string;
    onExternalImageDetected: () => void;
    getProxiedUrl: (url: string) => string;
}

export const MIN_IMAGE_SIZE = 4;


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
            const imageNode = node as HTMLImageElement;

            // Detect and remove pixel trackers (hidden images or very small images)
            const isHidden = imageNode.style.display === "none" || imageNode.style.visibility === "hidden";

            // Prefer attribute width/height over styles, but evaluate both
            let width = Infinity, height = Infinity;

            if (imageNode.hasAttribute("width")) {
                width = parseInt(imageNode.getAttribute("width")!, 10);
            } else if (imageNode.style.width) {
                width = parseDimension(imageNode.style.width);
            }

            if (imageNode.hasAttribute("height")) {
                height = parseInt(imageNode.getAttribute("height")!, 10);
            } else if (imageNode.style.height) {
                height = parseDimension(imageNode.style.height);
            }

            if (isHidden || Math.max(width, height) < MIN_IMAGE_SIZE) {
                imageNode.remove();
                return;
            }

            // Add lazy loading to all images
            imageNode.setAttribute("loading", "lazy");

            const src = imageNode.getAttribute("src");

            // Transform CID references to blob URLs
            if (src && src.startsWith("cid:") && cidToBlobUrlMap.size > 0) {
                const cid = src.substring(4); // Remove 'cid:' prefix
                const blobUrl = cidToBlobUrlMap.get(cid);
                if (blobUrl) {
                    imageNode.setAttribute("src", blobUrl);
                }
                return;
            }

            // Handle external images
            if (src?.startsWith("http") && externalImageOptions) {
                externalImageOptions.onExternalImageDetected();

                if (!externalImageOptions.canDisplayExternalImages || !externalImageOptions.displayExternalImages) {
                    imageNode.remove();
                    return;
                }

                // Proxy external images
                imageNode.setAttribute("src", externalImageOptions.getProxiedUrl(src));
            }
        }
    });

    return domPurify.sanitize(content, {
        FORBID_TAGS: ["script", "object", "iframe", "embed", "audio", "video"],
        ADD_ATTR: ["target", "rel"],
    });
}
