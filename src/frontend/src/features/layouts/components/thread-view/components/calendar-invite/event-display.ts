/**
 * Event display rules — clean and deduplicate fields before rendering.
 *
 * Single entry point: cleanEventForDisplay()
 *
 * Ported from the Calendars app so messages renders ICS invites with the
 * same polish (Google-style conference blocks stripped, known prefixes
 * removed, duplicate fields collapsed).
 */

/** Prefixes stripped from the Location field (case-insensitive). */
const LOCATION_PREFIXES_TO_STRIP = [
    "Pour participer à la visioconférence, cliquez sur ce lien : ",
];

/**
 * Embedded conference block delimited by ~:~ markers.
 * Contains a video-conference URL. Providers inject this in descriptions.
 */
// Not using the /s (dotAll) flag — tsconfig targets ES2017. `[\s\S]` is the
// portable equivalent for "any character including newlines".
const CONFERENCE_BLOCK_RE =
    /-::~:~::~:~[:~]*::~:~::-\s*\n[\s\S]*?(https:\/\/\S+)[\s\S]*?\n[\s\S]*?-::~:~::~:~[:~]*::~:~::-/;

const URL_RE = /https?:\/\/[^\s]+/i;

/**
 * Hostnames trusted to appear in a conference block. Anything else is
 * treated as not-a-conference-URL so an attacker can't lift a phishing link
 * out of a free-text description into the prominent "videocam" row.
 *
 * Entries match the hostname exactly OR as a suffix preceded by a dot
 * (so "zoom.us" also matches "company.zoom.us" if it were listed).
 *
 * Kept intentionally tight (Google only) — every entry here is a known
 * generator of the ``~:~`` block format. Add others on demand when we
 * have evidence they emit the same format and a reason to elevate their
 * link into the conference slot.
 */
const CONFERENCE_HOST_ALLOWLIST: readonly string[] = [
    "meet.google.com",
];

const isAllowedConferenceUrl = (url: string): boolean => {
    let host: string;
    try {
        host = new URL(url).hostname.toLowerCase();
    } catch {
        return false;
    }
    return CONFERENCE_HOST_ALLOWLIST.some(
        (h) => host === h || host.endsWith("." + h),
    );
};

export type EventDisplayFields = {
    description: string;
    location: string;
    url: string;
};

/**
 * Clean and deduplicate event fields for display.
 *
 * Applies in order:
 *  1. Trim whitespace on all fields
 *  2. Strip known prefixes from location
 *  3. Extract embedded conference URL from description → url (if url empty)
 *  4. Deduplicate: desc==location → empty desc,
 *     location==url → empty location, desc==url → empty desc
 */
export const cleanEventForDisplay = (
    raw: EventDisplayFields,
): EventDisplayFields => {
    let description = raw.description.trim();
    let location = stripLocationPrefixes(raw.location.trim());
    let url = raw.url.trim();

    if (!url) {
        const extracted = extractConferenceBlock(description);
        if (extracted.url) {
            description = extracted.description;
            url = extracted.url;
        }
    }

    if (description && description === location) description = "";
    if (location && location === url) location = "";
    if (description && description === url) description = "";

    return { description, location, url };
};

/** Extract the first URL found anywhere in a string. */
export const extractUrl = (text: string): string | null => {
    const match = text.match(URL_RE);
    return match ? match[0] : null;
};

const stripLocationPrefixes = (value: string): string => {
    for (const prefix of LOCATION_PREFIXES_TO_STRIP) {
        if (value.toLowerCase().startsWith(prefix.toLowerCase())) {
            return value.slice(prefix.length).trim();
        }
    }
    return value;
};

const extractConferenceBlock = (
    text: string,
): { description: string; url: string | null } => {
    const match = text.match(CONFERENCE_BLOCK_RE);
    if (!match) return { description: text, url: null };
    const url = match[1] ?? null;
    // Only surface URLs that resolve to a known conference provider —
    // free-text descriptions can otherwise smuggle a phishing link into
    // the prominent "videocam" slot.
    if (!url || !isAllowedConferenceUrl(url)) {
        return { description: text, url: null };
    }
    return {
        description: text.replace(match[0], "").trim(),
        url,
    };
};
