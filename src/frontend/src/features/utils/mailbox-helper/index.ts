import { MailboxAdmin, MailboxAdminCreate } from "@/features/api/gen";
/**
 * Helper class for operations on Mailbox resources.
 */

class MailboxHelper {
    /**
     * Returns the string representation of a Mailbox resource.
     * Actually it returns the email address of the mailbox.
     */
    static toString(mailbox: MailboxAdmin | MailboxAdminCreate): string {
        return `${mailbox.local_part}@${mailbox.domain_name}`;
    }
}

export default MailboxHelper;
