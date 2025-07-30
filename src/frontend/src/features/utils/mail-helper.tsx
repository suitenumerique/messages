import { renderToString } from "react-dom/server";
import { Markdown } from "@react-email/components";
import DetectionMap from "@/features/i18n/attachments-detection-map.json";
import React from "react";
import { z } from "zod";
import { DriveFile } from "../forms/components/message-form/drive-attachment-picker";

type ImapConfig = {
    host: string;
    port: number;
    use_ssl: boolean;
}

export const SUPPORTED_IMAP_DOMAINS = new Map<string, ImapConfig>([
    ["orange.fr", { host: "imap.orange.fr", port: 993, use_ssl: true }],
    ["wanadoo.fr", { host: "imap.orange.fr", port: 993, use_ssl: true }],
    ["gmail.com", { host: "imap.gmail.com", port: 993, use_ssl: true }]
]);

/* /!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\
   DO NOT EDIT EXISTING VALUE OF `ATTACHMENT_SEPARATORS`, ADD A NEW ONE
   If you want to change the separator, you must add a new value in the array
   Otherwise, previous messages will not be able to be parsed correctly
   /!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\/!\ */
export const ATTACHMENT_SEPARATORS = ['---------- Drive attachments ----------'];
const ATTACHMENT_SEPARATOR = ATTACHMENT_SEPARATORS[ATTACHMENT_SEPARATORS.length - 1];

/** An helper which aims to gather all utils related write and send a message */
class MailHelper {

    /**
     * Take a Markdown string
     * then render HTML ready for email through react-email.
     */
    static async markdownToHtml(markdown: string) {
        return renderToString(<Markdown>{markdown}</Markdown>);
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
     */
    static isValidEmail(email: string): boolean {
        return z.string().email().safeParse(email).success;
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

        return SUPPORTED_IMAP_DOMAINS.get(domain)!;
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
    static areAttachmentsMentionedInDraft(draftText: string): boolean {
        const keyWordsAttachments = MailHelper.getAttachmentKeywords(DetectionMap);
        const messageEditorDraft = draftText?.toLowerCase() || "";
        return keyWordsAttachments.some((keyword) => messageEditorDraft.includes(keyword));
    }

    /**
     * Attach drive attachments to a draft.
     * Attachments are serialized as a JSON string and appended to the draft.
     */
    static attachDriveAttachmentsToDraft(draft: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return draft;
        return draft
        + ATTACHMENT_SEPARATOR
        + JSON.stringify(attachments);
    }

    /**
     * Attach drive attachments to a text body.
     * Append attachments as a list of markdown links [name](url).
     */
    static attachDriveAttachmentsToTextBody(textBody: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return textBody;
        return textBody
        + `\n${ATTACHMENT_SEPARATOR}\n`
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
        + `\n${ATTACHMENT_SEPARATOR}\n`
        + `<ul>\n`
        + attachments.map(
            a => '<li>\n'
            +`<a class="drive-attachment" href="${a.url}" data-id="${a.id}" data-name="${a.name}" data-type="${a.type}" data-size="${a.size}" data-created_at="${a.created_at}">`
            + a.name
            + '</a>\n'
            + '</li>'
            ).join('\n')
        + `\n</ul>\n\n`;
    }

    /**
     * Extract drive attachments from a draft.
     */
    static extractDriveAttachmentsFromDraft(draft: string = ''): [string, DriveFile[]] {
        const [draftBody, driveAttachments = '[]'] = draft.split(new RegExp(`${ATTACHMENT_SEPARATORS.join('|')}`, 's'));
        let attachments = [];
        try {
            attachments = JSON.parse(driveAttachments);
        } catch (e) {
            console.error('Cannot parse drive attachments', e);
        }
        return [draftBody, attachments];
    }

    /**
     * Extract drive attachments from text body.
     */
    static extractDriveAttachmentsFromTextBody(text: string = ''): [string, Pick<DriveFile, 'name' | 'url'>[]] {
        const regex = new RegExp(`\n(${ATTACHMENT_SEPARATORS.join('|')})\n(.*)\n\n`, 's');
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
     * Extract drive attachments from html body.
     */
    static extractDriveAttachmentsFromHtmlBody(html: string = ''): [string, DriveFile[]] {
        const regex = new RegExp(`\n(${ATTACHMENT_SEPARATORS.join('|')})\n<ul>\n(.*?)\n</ul>\n\n`, 's');
        const matches = html.match(regex);
        if (!matches) return [html, []];

        // Join the attachment parts and parse anchor elements
        const attachments: DriveFile[] = [];

        // Parse anchor elements with drive-attachment class
        const anchorRegex = /<a[^>]*class="drive-attachment"[^>]*>.*<\/a>/g;
        let anchorMatch;

        while ((anchorMatch = anchorRegex.exec(matches[2])) !== null) {
            const anchorElement = anchorMatch[0];

            // Extract data attributes
            const extractDataAttribute = (attr: string): string | null => {
                const regex = new RegExp(`data-${attr}="([^"]*)"`, 'i');
                const anchorMatch = anchorElement.match(regex);
                return anchorMatch ? anchorMatch[1] : null;
            };

            const id = extractDataAttribute('id');
            const name = extractDataAttribute('name');
            const type = extractDataAttribute('type');
            const sizeStr = extractDataAttribute('size');
            const created_at = extractDataAttribute('created_at');

            // Extract href attribute
            const hrefMatch = anchorElement.match(/href="([^"]*)"/);
            const url = hrefMatch ? hrefMatch[1] : '';

            if (id && name && url) {
                attachments.push({
                    id,
                    name,
                    type: type || 'application/octet-stream',
                    size: parseInt(sizeStr || '0', 10),
                    created_at: created_at || '',
                    url
                });
            } else {
                console.error('Cannot extract drive attachment from anchor element.', anchorElement)
            }
        }

        return [html.replace(regex, '').trim(), attachments];
    }
}

export default MailHelper;
