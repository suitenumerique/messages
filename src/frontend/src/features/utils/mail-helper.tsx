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
}

export default MailHelper;
