import { renderToString } from "react-dom/server";
import { Markdown } from "@react-email/components";
import React from "react";

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
        // Trim whitespace and validate format: something@something.something
        const emailRegex = /^((?!\.)[\w\-_.]*[^.])(@\w+)(\.\w+(\.\w+)?[^.\W])$/;
        return emailRegex.test(email);
    }
}

export default MailHelper;