import { Mailbox } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";

/**
 * Resolves the mailbox a compose window sends from.
 *
 * A compose window keeps the mailbox it was opened from, whichever one the
 * user is currently browsing, so every surface that lists windows — docked
 * tabs, the "+X" overflow menu, the mobile stack bar and overview — has to
 * name its sender.
 *
 * The returned getter yields `undefined` when the user owns a single mailbox:
 * there is then nothing to disambiguate and the marker is pure noise, the same
 * rule the message form applies to its "From" field. Centralising it here
 * keeps those surfaces from drifting apart.
 *
 */
export const useComposeSender = (): ((mailboxId: string) => Mailbox | undefined) => {
    const { mailboxes } = useMailboxContext();

    return (mailboxId: string) =>
        mailboxes && mailboxes.length > 1
            ? mailboxes.find((mailbox) => mailbox.id === mailboxId)
            : undefined;
};
