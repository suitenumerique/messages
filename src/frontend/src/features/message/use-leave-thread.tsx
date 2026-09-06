import { useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { MailboxRoleChoices, ThreadAccessRoleChoices } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import useDeleteThreadAccess from "./use-delete-thread-access";

/**
 * Leaving the selected thread = deleting the access of the user's own
 * mailbox, behind an explicit confirmation. Shared by the desktop action
 * bar dropdown and the mobile more-options drawer.
 *
 * `canLeaveThread` is false for viewer mailboxes (they cannot manage their
 * access) and for the last editor (the thread would become orphaned).
 */
const useLeaveThread = () => {
    const { t } = useTranslation();
    const modals = useModals();
    const { selectedMailbox, selectedThread } = useMailboxContext();
    const { deleteThreadAccess } = useDeleteThreadAccess();
    const mailboxAccess = selectedThread?.accesses.find((a) => a.mailbox.id === selectedMailbox?.id);
    const hasOnlyOneEditor = selectedThread?.accesses.filter((a) => a.role === ThreadAccessRoleChoices.editor).length === 1;
    const canLeaveThread = Boolean(
        selectedMailbox?.role !== MailboxRoleChoices.viewer
        && mailboxAccess
        && selectedThread
        && (!hasOnlyOneEditor || mailboxAccess.role !== ThreadAccessRoleChoices.editor)
    );

    const leaveThread = async () => {
        if (!mailboxAccess || !selectedThread) return;
        const decision = await modals.deleteConfirmationModal({
            title: t('Leave this thread?'),
            children: t(
                'You and all users with access to the mailbox \"{{mailboxName}}\" will no longer see this thread.',
                { mailboxName: mailboxAccess.mailbox.email }
            ),
        });
        if (decision !== 'delete') return;
        deleteThreadAccess({
            accessId: mailboxAccess.id,
            accessMailboxId: mailboxAccess.mailbox.id,
            threadId: selectedThread.id,
            onSuccess: () => {
                addToast(<ToasterItem><p>{t('You left the thread')}</p></ToasterItem>);
            },
        });
    };

    return { canLeaveThread, leaveThread };
};

export default useLeaveThread;
