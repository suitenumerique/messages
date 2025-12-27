/**
 * Renderer for image/* body parts.
 * Content is base64 encoded by the backend.
 */

/**
 * Render an image body part to HTML.
 * If cid is provided and found in the map, uses blob URL; otherwise creates data URL from base64.
 */
export function renderImage(
    type: string,
    content: string,
    cid: string | undefined,
    cidToBlobUrlMap: Map<string, string>
): string {
    if (cid && cidToBlobUrlMap.has(cid)) {
        return `<img src="${cidToBlobUrlMap.get(cid)}" loading="lazy" alt="" />`;
    }

    if (content) {
        return `<img src="data:${type};base64,${content}" loading="lazy" alt="" />`;
    }

    return "";
}

