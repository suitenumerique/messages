import { MailboxAdmin, MailboxAdminCreate } from "@/features/api/gen";

/** Minimal shape needed to order mailboxes by kind (personal vs shared). */
type MailboxKind = {
    is_identity: boolean;
    email: string;
};

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

    /**
     * Sorts mailboxes for display: personal mailboxes (identities) first, then
     * shared ones, each group ordered alphabetically by email address.
     *
     * @returns a new sorted array (the input is left untouched).
     */
    static sortByKind<T extends MailboxKind>(mailboxes: readonly T[]): T[] {
        return [...mailboxes].sort((a, b) => {
            const identityDiff = Number(b.is_identity) - Number(a.is_identity);
            if (identityDiff !== 0) return identityDiff;
            return a.email.localeCompare(b.email);
        });
    }

    /**
     * Tells whether a visual separator should follow the mailbox at `index` in a
     * list already sorted by {@link sortByKind}. True only on the last personal
     * mailbox right before the first shared one.
     */
    static showSeparatorAfter(
        sortedMailboxes: readonly Pick<MailboxKind, "is_identity">[],
        index: number,
    ): boolean {
        return (
            sortedMailboxes[index].is_identity &&
            sortedMailboxes[index + 1]?.is_identity === false
        );
    }
}

export default MailboxHelper;
