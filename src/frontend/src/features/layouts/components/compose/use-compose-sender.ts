import { useTranslation } from "react-i18next";
import { Mailbox } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";

/**
 * Resolves the mailbox a compose window sends from.
 *
 * A compose window keeps the mailbox it was opened from, whichever one the
 * user is currently browsing, so every surface that lists windows — docked
 * tabs, the "+X" overflow menu, the mobile stack bar and overview — has to
 * name its sender. That sender follows the form's "From" field once the form
 * reported it, and falls back to the opening mailbox until then.
 *
 * The returned getter yields `undefined` when the user owns a single mailbox:
 * there is then nothing to disambiguate and the marker is pure noise, the same
 * rule the message form applies to its "From" field. Centralising it here
 * keeps those surfaces from drifting apart.
 */
export const useComposeSender = (): ((window: Pick<ComposeWindowDescriptor, "mailboxId" | "senderId">) => Mailbox | undefined) => {
    const { mailboxes } = useMailboxContext();

    return ({ mailboxId, senderId }) =>
        mailboxes && mailboxes.length > 1
            ? mailboxes.find((mailbox) => mailbox.id === (senderId ?? mailboxId))
            : undefined;
};

/**
 * Spelled-out sender for the surfaces listing windows as text (overflow
 * menu, mobile overview), flagging a draft transfer still in flight.
 */
export const useComposeSenderText = (): ((window: Pick<ComposeWindowDescriptor, "mailboxId" | "senderId" | "isSenderPending">) => string | undefined) => {
    const { t } = useTranslation();
    const getSender = useComposeSender();

    return (window) => {
        const email = getSender(window)?.email;
        if (!email || !window.isSenderPending) return email;
        return t("Transferring to {{email}}…", { email });
    };
};
