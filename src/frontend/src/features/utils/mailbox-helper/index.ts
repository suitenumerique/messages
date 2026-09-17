import type { Mailbox, MailboxAdmin, MailboxAdminCreate } from "@/features/api/gen";
// Imported from the model file rather than the `api/gen` barrel: the barrel
// pulls `fetch-api`, which pulls `auth`, which pulls this helper. A value
// used at module level from inside that cycle would be `undefined`.
import { MailboxRoleChoices } from "@/features/api/gen/models/mailbox_role_choices";
import { LAST_ACTIVE_MAILBOX_KEY } from "@/features/config/constants";
import MailHelper from "@/features/utils/mail-helper";

/** Minimal shape needed to order mailboxes by kind (personal vs shared). */
type MailboxKind = {
    is_identity: boolean;
    email: string;
};

/**
 * Record persisted on the device for the "last active mailbox".
 *
 * It binds the mailbox to the user it was selected by: a session that merely
 * expires keeps the record (so the user lands back where they were), while a
 * voluntary logout clears it (`logout`). The `userId` guard is what keeps the
 * record from leaking across accounts when another user signs in on the same
 * browser after an expired session.
 */
type LastActiveMailbox = {
    userId: string;
    mailboxId: string;
};

export type ResolveSelectedMailboxOptions = {
    /** Mailbox id carried by the current URL, if any. */
    routeMailboxId?: string;
    /** Mailbox id remembered on this device for the signed-in user, if any. */
    lastActiveMailboxId?: string;
    /** Email of the signed-in user, used to find their personal mailbox. */
    userEmail?: string | null;
};

const ROLE_PRIORITY: readonly MailboxRoleChoices[] = [
    MailboxRoleChoices.admin,
    MailboxRoleChoices.editor,
    MailboxRoleChoices.sender,
    MailboxRoleChoices.viewer,
];

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

    /**
     * Read the mailbox id remembered on this device for `userId`, ignoring a
     * record left by another user or a corrupted one.
     */
    static readLastActiveMailboxId(userId: string | undefined): string | undefined {
        if (!userId) return undefined;
        const raw = localStorage.getItem(LAST_ACTIVE_MAILBOX_KEY);
        if (!raw) return undefined;
        try {
            const record = JSON.parse(raw) as Partial<LastActiveMailbox>;
            if (record.userId !== userId || typeof record.mailboxId !== "string") return undefined;
            return record.mailboxId;
        } catch {
            return undefined;
        }
    }

    /** Remember `mailboxId` as the last active mailbox of `userId` on this device. */
    static persistLastActiveMailbox(userId: string, mailboxId: string): void {
        const record: LastActiveMailbox = { userId, mailboxId };
        localStorage.setItem(LAST_ACTIVE_MAILBOX_KEY, JSON.stringify(record));
    }

    /** Forget the last active mailbox remembered on this device. */
    static clearLastActiveMailbox(): void {
        localStorage.removeItem(LAST_ACTIVE_MAILBOX_KEY);
    }

    /**
     * The user's own mailbox: an identity mailbox whose local part matches the
     * local part of the signed-in user's email. When several domains carry the
     * same local part, the mailbox with the exact same address wins.
     */
    static findPersonalMailbox(
        mailboxes: readonly Mailbox[],
        userEmail: string | null | undefined,
    ): Mailbox | undefined {
        if (!userEmail) return undefined;
        const userLocalPart = this.getLocalPart(userEmail);
        if (userLocalPart === undefined) return undefined;
        const candidates = mailboxes.filter(
            (mailbox) => mailbox.is_identity && this.getLocalPart(mailbox.email) === userLocalPart,
        );
        const normalizedUserEmail = MailHelper.asciiLower(userEmail);
        return candidates.find((mailbox) => MailHelper.asciiLower(mailbox.email) === normalizedUserEmail)
            ?? candidates[0];
    }

    /**
     * Last-resort fallback: the mailbox granting the most privileges. `findLast`
     * keeps the historical behaviour of picking the most recently listed one
     * among equals.
     */
    static findMostPrivilegedMailbox(mailboxes: readonly Mailbox[]): Mailbox | undefined {
        for (const role of ROLE_PRIORITY) {
            const mailbox = mailboxes.findLast((m) => m.role === role);
            if (mailbox) return mailbox;
        }
        return mailboxes[mailboxes.length - 1];
    }

    /**
     * Pick the mailbox to display, in order of preference:
     * 1. the one addressed by the URL,
     * 2. the one last active on this device for this user,
     * 3. the user's personal mailbox,
     * 4. the mailbox with the highest privileges.
     */
    static resolveSelectedMailbox(
        mailboxes: readonly Mailbox[],
        { routeMailboxId, lastActiveMailboxId, userEmail }: ResolveSelectedMailboxOptions,
    ): Mailbox | null {
        if (!mailboxes.length) return null;
        const findById = (id: string | undefined) =>
            id ? mailboxes.find((mailbox) => mailbox.id === id) : undefined;

        return findById(routeMailboxId)
            ?? findById(lastActiveMailboxId)
            ?? this.findPersonalMailbox(mailboxes, userEmail)
            ?? this.findMostPrivilegedMailbox(mailboxes)
            ?? null;
    }

    private static getLocalPart(email: string): string | undefined {
        const localPart = MailHelper.splitEmail(email)?.[0];
        return localPart === undefined ? undefined : MailHelper.asciiLower(localPart);
    }
}

export default MailboxHelper;
