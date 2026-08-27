import { renderToStaticMarkup } from "react-dom/server";
import DetectionMap from "@/features/i18n/attachments-detection-map.json";
import i18n from "@/features/i18n/initI18n";
import z from "zod";
import { DriveFile } from "../forms/components/message-form/drive-attachment-picker";
import { handle } from "./errors";
import { getBlobDownloadRetrieveUrl } from "@/features/api/gen/blob/blob";
import { getApiOrigin } from "@/features/api/utils";

/**
 * Decode HTML entities produced by renderToStaticMarkup in attribute values.
 * &amp; must be decoded last to avoid double-decoding (e.g. &amp;lt; → &lt; → <).
 */
const decodeHtmlEntities = (str: string): string =>
    str.replace(/&lt;/g, '<')
       .replace(/&gt;/g, '>')
       .replace(/&quot;/g, '"')
       .replace(/&#x27;/g, "'")
       .replace(/&amp;/g, '&');

type ImapConfig = {
    host: string;
    port: number;
    use_ssl: boolean;
}

export const IMAP_DOMAIN_REGEXES = new Map<string, string>([
    ["orange", "orange\.fr"],
    ["wanadoo", "wanadoo\.fr"],
    ["gmail", "(gmail\.com|googlemail\.com)"],
    ["yahoo", "yahoo\.(?:[a-z]{2,4}|[a-z]{2}\.[a-z]{2})"],
]);

export const SUPPORTED_IMAP_DOMAINS = new Map<string, ImapConfig>([
    [IMAP_DOMAIN_REGEXES.get("orange")!, { host: "imap.orange.fr", port: 993, use_ssl: true }],
    [IMAP_DOMAIN_REGEXES.get("wanadoo")!, { host: "imap.orange.fr", port: 993, use_ssl: true }],
    [IMAP_DOMAIN_REGEXES.get("gmail")!, { host: "imap.gmail.com", port: 993, use_ssl: true }],
    [IMAP_DOMAIN_REGEXES.get("yahoo")!, { host: "imap.mail.yahoo.com", port: 993, use_ssl: true }],
]);

/* /!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\
   DO NOT EDIT EXISTING VALUE OF `ATTACHMENT_SEPARATORS`, ADD A NEW ONE
   If you want to change the separator, you must add a new value in the array
   Otherwise, previous messages will not be able to be parsed correctly
   /!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\ */
export const ATTACHMENT_SEPARATORS = [
    '---------- Drive attachments ----------',
    '---------- Fichiers joints ----------',
    '---------- Drive-bijlagen ----------',
];

// Active separator used when sending a new message, keyed by i18n language code.
// Unknown languages fall back to the legacy English value (first entry above).
const ATTACHMENT_SEPARATORS_BY_LANG: Record<string, string> = {
    'en-US': '---------- Drive attachments ----------',
    'fr-FR': '---------- Fichiers joints ----------',
    'nl-NL': '---------- Drive-bijlagen ----------',
};

const getAttachmentSeparator = (): string =>
    ATTACHMENT_SEPARATORS_BY_LANG[i18n.language] ?? ATTACHMENT_SEPARATORS[0];

const escapeRegex = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Regex source matching the path of a blob download URL, with the blob id as
 * first group.
 *
 * Derived from the Orval-generated getBlobDownloadRetrieveUrl so it stays in
 * sync with the API spec. Carries no origin: callers prepend the prefix that
 * matches how strict they need to be.
 */
const blobUrlRegexSource = (): string => {
    const placeholder = '__BLOB_ID__';
    // Escape regex special chars in the template, then replace the placeholder with a capture group
    return escapeRegex(getBlobDownloadRetrieveUrl(placeholder))
        .replace(placeholder, '([a-f0-9-]+)');
};

/** Matches only if every character is ASCII. */
const ASCII_ONLY = /^[\x00-\x7F]*$/;

/**
 * Zod's own `z.email()` pattern is ASCII-only, which would reject the
 * accented addresses we want to accept-and-warn-about; its `unicodeEmail`
 * alternative is `/^[^\s@"]{1,64}@[^\s@]{1,255}$/u`, which accepts `a@com`
 * and `a@example..com`. So this is `z.email()`'s shape with the letter
 * classes widened to Unicode, keeping the structural checks the ASCII one
 * has: at least two labels, no empty label, no leading or doubled dot in
 * the local part.
 *
 * One check is deliberately looser than `z.email()`: its TLD is
 * `[A-Za-z]{2,}`, which would reject a punycode TLD such as `xn--p1ai`, so
 * digits and hyphens are allowed after the first letter.
 */
export const UNICODE_EMAIL_REGEX = new RegExp(
    // local part: max 64, no leading dot, no consecutive dots, no trailing dot
    "^(?=.{1,64}@)(?!\\.)(?!.*\\.\\.)[\\p{L}\\p{M}\\p{N}_'+\\-.]*[\\p{L}\\p{M}\\p{N}_+-]"
    // domain: max 255, one or more labels, then a TLD of 2+ starting with a letter
    + '@(?=.{1,255}$)(?:[\\p{L}\\p{M}\\p{N}][\\p{L}\\p{M}\\p{N}\\-]*\\.)+'
    + '\\p{L}[\\p{L}\\p{M}\\p{N}\\-]*[\\p{L}\\p{M}\\p{N}]$',
    'u'
);

/** An helper which aims to gather all utils related write and send a message */
class MailHelper {

    /**
     * Replace blob download URLs in HTML with cid: references for email embedding.
     * This converts image sources from API URLs to Content-ID references
     * that email clients can resolve using the MIME multipart/related structure.
     */
    static replaceBlobUrlsWithCid(html: string): string {
        // Origin-agnostic on purpose: a draft written against another API origin
        // (dev vs prod, mobile webview) must still get its images embedded, and
        // rewriting a foreign URL to a cid: reference only ever removes a remote
        // fetch from the sent email.
        return html.replace(new RegExp(`(?:https?://[^/]+)?${blobUrlRegexSource()}`, 'g'), 'cid:$1');
    }

    /**
     * Reads the blob id out of a blob download URL.
     *
     * Fully anchored, and restricted to our own API: the URL must *be* one of
     * the download URLs we built, not merely start with or contain something
     * that looks like one. A caller acts on the returned id (dropping the image
     * block that carries it), so a URL hosted elsewhere must not be able to
     * impersonate an attachment.
     *
     * @param url - the URL to inspect
     * @returns the blob id, or `null` for any other URL — a remote address, a
     *   `data:` URI or a hand-typed link never went through our upload, so it
     *   has no attachment to be matched against.
     */
    static extractBlobId(url: string): string | null {
        if (!url) return null;
        const apiOrigin = getApiOrigin();
        const originPrefix = apiOrigin ? `(?:${escapeRegex(apiOrigin)})?` : '';
        return new RegExp(`^${originPrefix}${blobUrlRegexSource()}$`).exec(url)?.[1] ?? null;
    }

    /**
     * Prefix the subject of a message if it doesn't already start with the prefix.
     */
    static prefixSubjectIfNeeded(subject: string, prefix: string = 'Re:') {
        return subject.startsWith(prefix) ? subject : `${prefix} ${subject}`;
    }

    /**
     * Parse a string of recipients separated by commas
     * and return an array of recipients.
     */
    static parseRecipients(recipients: string) {
        return recipients.split(',').map(recipient => recipient.trim());
    }

    /**
     * Validate an array of recipients, all values must be valid email addresses.
     */
    static areRecipientsValid(recipients: string[] | undefined = [], required: boolean = true) {
        if (required && (recipients.length === 0)) {
            return false;
        }
        if (!recipients.every(r => this.isValidEmail(r))) {
            return false;
        }
        return true;
    }

    /**
     * Test if an email address is valid.
     *
     * Unicode-aware on purpose: the backend can send to an accented domain
     * (it IDNA-encodes it on the way out), so refusing those here would
     * reject addresses that actually work. Accented *local* parts are not
     * supported and are surfaced by `hasNonAsciiLocalPart` instead of being
     * silently unselectable.
     */
    static isValidEmail(email: string): boolean {
        return z.email({ pattern: UNICODE_EMAIL_REGEX }).safeParse(email).success;
    }

    /**
     * Lowercase `A-Z` and nothing else. Mirrors the backend's `ascii_lower`.
     *
     * Use this, never `toLowerCase()`, on a local part. Unicode lowercasing
     * maps non-ASCII code points onto ASCII (U+212A KELVIN SIGN becomes "k"),
     * so `nicK` would fold onto an existing `nick` and collide with someone
     * else's mailbox.
     *
     * A domain is the opposite case and deliberately uses `toLowerCase()`:
     * DNS is case-insensitive and UTS-46 performs exactly that mapping.
     */
    static asciiLower(value: string): string {
        return value.replace(/[A-Z]/g, (char) =>
            String.fromCharCode(char.charCodeAt(0) + 32)
        );
    }

    /**
     * Split an address into [localPart, domain], or undefined if malformed.
     * Splits on the last "@", which is the domain separator.
     */
    static splitEmail(email: string): [string, string] | undefined {
        const at = email.lastIndexOf('@');
        if (at <= 0 || at === email.length - 1) return undefined;
        return [email.slice(0, at), email.slice(at + 1)];
    }

    /**
     * Lowercase the domain of an address, leaving the local part alone.
     *
     * DNS is case-insensitive so the domain has one canonical spelling, but
     * RFC 5321 §2.4 leaves the local part to the destination host — we are
     * not it, so we send back exactly what was typed.
     *
     * Returns the input unchanged when it has no domain to normalize.
     */
    static normalizeEmailDomain(email: string): string {
        const parts = this.splitEmail(email.trim());
        if (!parts) return email.trim();
        return `${parts[0]}@${parts[1].toLowerCase()}`;
    }

    /** True when the local part carries a character outside ASCII. */
    static hasNonAsciiLocalPart(email: string): boolean {
        const parts = this.splitEmail(email);
        return !!parts && !ASCII_ONLY.test(parts[0]);
    }

    /** True when the domain carries a character outside ASCII (an IDN). */
    static hasNonAsciiDomain(email: string): boolean {
        const parts = this.splitEmail(email);
        return !!parts && !ASCII_ONLY.test(parts[1]);
    }

    /**
     * Get the domain from an email address.
     */
    static getDomainFromEmail(email: string) {
        if (!this.isValidEmail(email)) return undefined;
        return email.split('@')[1];
    }

    /**
     * Get the IMAP config for a given email address
     * if the domain is a supported one (see SUPPORTED_IMAP_DOMAINS)
     */
    static getImapConfigFromEmail(email: string): ImapConfig | undefined {
        const domain = this.getDomainFromEmail(email);
        if (!domain) return undefined;

        return Array
            .from(SUPPORTED_IMAP_DOMAINS.entries())
            .find(([regex]) => new RegExp(`^${regex}$`).test(domain))?.[1];
    }

    /**
     * Get all keywords for attachment detection from the detection map.
     */
    static getAttachmentKeywords(detectionMap: Record<string, Record<string, string[]>>): string[] {
        const allKeywords = new Set<string>();
        Object.values(detectionMap).forEach((langObj) => {
            Object.values(langObj).forEach((arr) => {
                (arr as string[]).forEach((kw) => allKeywords.add(kw.toLowerCase()));
            });
        });
        return Array.from(allKeywords);
    }

    /**
     * Check if any attachment keyword is mentioned in the draft text.
     */
    static areAttachmentsMentionedInDraft(draftText: string = ''): boolean {
        const patterns = MailHelper.getAttachmentKeywords(DetectionMap);
        return patterns.some((pattern) => {
            const isRegex = pattern.startsWith('/') && pattern.endsWith('/');
            if (isRegex) {
                try {
                    return new RegExp(pattern.slice(1, -1), 'i').test(draftText);
                } catch (error) {
                    handle(new Error(`Invalid regex pattern "${pattern}".`), { extra: { error } });
                    return false;
                }
            }
            return draftText.toLowerCase().includes(pattern);
        });
    }

    /**
     * Attach drive attachments to a draft.
     * Attachments are serialized as a JSON string and appended to the draft.
     */
    static attachDriveAttachmentsToDraft(draft: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return draft;
        return draft
        + getAttachmentSeparator()
        + JSON.stringify(attachments);
    }

    /**
     * Attach drive attachments to a text body.
     * Append attachments as a list of markdown links [name](url).
     */
    static attachDriveAttachmentsToTextBody(textBody: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return textBody;
        return textBody
        + `\n${getAttachmentSeparator()}\n`
        + attachments.map(a =>
            `- [${a.name}](${a.url})`
        ).join('\n')
        + '\n\n';
    }

    /**
     * Attach drive attachments to a html body.
     * Append attachments as a list of html links <a href="url">name</a> with data attributes.
     */
    static attachDriveAttachmentsToHtmlBody(htmlBody: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return htmlBody;
        return htmlBody
        + `\n${getAttachmentSeparator()}\n`
        + renderToStaticMarkup(
            <ul>
                {attachments.map((a) => (
                    <li key={a.id}>
                        <a className="drive-attachment" href={a.url} data-id={a.id} data-name={a.name} data-type={a.type} data-size={String(a.size)} data-created_at={a.created_at}>{a.name}</a>
                    </li>
                ))}
            </ul>
        )
        + '\n\n';
    }

    /**
     * Extract drive attachments from a draft.
     */
    static extractDriveAttachmentsFromDraft(draft: string = ''): [string, DriveFile[]] {
        const [draftBody, driveAttachments = '[]'] = draft.split(new RegExp(`${ATTACHMENT_SEPARATORS.join('|')}`, 's'));
        let attachments = [];
        try {
            attachments = JSON.parse(driveAttachments);
        } catch (error) {
            handle(new Error('Cannot parse drive attachments.'), { extra: { error } });
        }
        return [draftBody, attachments];
    }

    /**
     * Extract drive attachments from text body.
     */
    static extractDriveAttachmentsFromTextBody(text: string = ''): [string, Pick<DriveFile, 'name' | 'url'>[]] {
        const regex = new RegExp(`\n(${ATTACHMENT_SEPARATORS.join('|')})[\n\r]*(.*)[\n\r]*`, 's');
        const matches = text.match(regex);
        if (!matches) return [text, []];

        const rawDriveAttachments = matches[2];
        const driveAttachments = rawDriveAttachments.split('\n').map(a => {
            const match = a.match(/^- \[(.*)\]\((.*)\)$/);
            if (!match) return undefined;
            return { name: match[1], url: match[2] };
        }).filter(a => a !== undefined);
        return [text.replace(regex, '').trim(), driveAttachments];
    }

    /**
     * Convert a data URL (base64-encoded) to a File object.
     * Returns null if the input is not a valid image data URL.
     */
    static dataUrlToFile(dataUrl: string, filename: string): File | null {
        const match = dataUrl.match(/^data:(image\/[\w+.-]+);base64,(.+)$/);
        if (!match) return null;

        const [, mimeType, base64Data] = match;
        try {
            const binaryString = atob(base64Data);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            return new File([bytes], filename, { type: mimeType });
        } catch {
            return null;
        }
    }

    /**
     * Extract drive attachments from html body.
     */
    static extractDriveAttachmentsFromHtmlBody(html: string = ''): [string, DriveFile[]] {
        const regex = new RegExp(`(${ATTACHMENT_SEPARATORS.join('|')})[\n\r]*<ul>\s*(.*?)\s*</ul>[\n\r]*`, 's');
        const matches = html.match(regex);
        if (!matches) return [html, []];

        // Join the attachment parts and parse anchor elements
        const attachments: DriveFile[] = [];

        // Parse anchor elements with drive-attachment class
        const anchorRegex = /<a[^>]*class="drive-attachment"[^>]*>.*?<\/a>/g;
        let anchorMatch;

        while ((anchorMatch = anchorRegex.exec(matches[2])) !== null) {
            const anchorElement = anchorMatch[0];

            // Extract data attributes
            const extractDataAttribute = (attr: string): string | null => {
                const regex = new RegExp(`data-${attr}="([^"]*)"`, 'i');
                const anchorMatch = anchorElement.match(regex);
                return anchorMatch ? decodeHtmlEntities(anchorMatch[1]) : null;
            };

            const id = extractDataAttribute('id');
            const name = extractDataAttribute('name');
            const type = extractDataAttribute('type');
            const sizeStr = extractDataAttribute('size');
            const created_at = extractDataAttribute('created_at');

            // Extract href attribute
            const hrefMatch = anchorElement.match(/href="([^"]*)"/);
            const url = hrefMatch ? decodeHtmlEntities(hrefMatch[1]) : '';

            if (id && name && url) {
                attachments.push({
                    id,
                    name,
                    type: type || 'application/octet-stream',
                    size: parseInt(sizeStr || '0', 10),
                    created_at: created_at || '',
                    url,
                });
            } else {
                handle(new Error('Cannot extract drive attachment from anchor element.'), { extra: { anchorElement } });
            }
        }

        return [html.replace(regex, '').trim(), attachments];
    }
}

export default MailHelper;
