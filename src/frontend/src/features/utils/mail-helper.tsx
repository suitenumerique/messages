import { renderToString } from "react-dom/server";
import { Markdown } from "@react-email/components";
import React from "react";
import { z } from "zod";

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
import { DriveFile } from "@/pages/drive-selection";

const ATTACHMENT_SEPARATOR = '— — -';

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
        if (!recipients.every(r => this.#isValidEmail(r))) {
            return false;
        }
        return true;
    }

    /**
     * Test if an email address is valid.
     */
    static #isValidEmail(email: string): boolean {
        return z.string().email().safeParse(email).success;
    }

    /**
     * Get the domain from an email address.
     */
    static getDomainFromEmail(email: string) {
        if (!this.#isValidEmail(email)) return undefined;
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
     * Attach drive attachments to a draft.
     */
    static attachDriveAttachmentsToDraft(draft: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return draft;
        return `${draft}${ATTACHMENT_SEPARATOR}${JSON.stringify(attachments)}`;
    }

    /**
     * Extract drive attachments from a draft.
     */
    static extractDriveAttachmentsFromDraft(draft: string = '') {
        const [draftBody, driveAttachments = '[]'] = draft.split(ATTACHMENT_SEPARATOR);
        return [draftBody, JSON.parse(driveAttachments)];
    }

    static attachDriveAttachmentsToTextBody(textBody: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return textBody;
        return `${textBody}\n${ATTACHMENT_SEPARATOR}\n${JSON.stringify(attachments)}`;
    }

    static extractDriveAttachmentsFromTextBody(text: string = '') {
        const [textBody, driveAttachments = '[]'] = text.split(`\n${ATTACHMENT_SEPARATOR}\n`);
        return [textBody, JSON.parse(driveAttachments)];
    }

    static attachDriveAttachmentsToHtmlBody(htmlBody: string = '', attachments: DriveFile[] = []) {
        if (attachments.length === 0) return htmlBody;
        return `${htmlBody}\n${ATTACHMENT_SEPARATOR}\n${attachments.map(a => `<a className="drive-attachment" href="${a.url}" data-id="${a.id}" data-name="${a.name}" data-type="${a.type}" data-size="${a.size}" data-created_at="${a.created_at}">${a.name}</a>`).join(ATTACHMENT_SEPARATOR)}`;
    }

    static extractDriveAttachmentsFromHtmlBody(html: string = ''): [string, DriveFile[]] {
        const parts = html.split(`\n${ATTACHMENT_SEPARATOR}\n`);
        const htmlBody = parts[0] || '';

        if (parts.length < 2) {
            return [htmlBody, []];
        }

        // Join the attachment parts and parse anchor elements
        const attachmentHtml = parts.slice(1).join(`\n${ATTACHMENT_SEPARATOR}\n`);
        const attachments: DriveFile[] = [];

        // Parse anchor elements with drive-attachment class
        const anchorRegex = /<a[^>]*className="drive-attachment"[^>]*>(.*?)<\/a>/g;
        let match;

        while ((match = anchorRegex.exec(attachmentHtml)) !== null) {
            const anchorElement = match[0];

            // Extract data attributes
            const extractDataAttribute = (attr: string): string => {
                const regex = new RegExp(`data-${attr}="([^"]*)"`, 'i');
                const match = anchorElement.match(regex);
                return match ? match[1] : '';
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
                    type,
                    size: parseInt(sizeStr, 10) || 0,
                    created_at,
                    url
                });
            }
        }

        return [htmlBody, attachments];
    }
}

export default MailHelper;
