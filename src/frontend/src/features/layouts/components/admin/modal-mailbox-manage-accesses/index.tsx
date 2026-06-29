import { ShareModal } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { MailboxAdmin, UserWithoutAbilities } from "@/features/api/gen";
import MailboxHelper from "@/features/utils/mailbox-helper";
import {
  NormalizedMailboxAccess,
  useMailboxAccessManagement,
} from "@/hooks/use-mailbox-access-management";

type ModalMailboxManageAccessesProps = {
    domainId: string;
    isOpen: boolean;
    onClose: () => void;
    mailbox: MailboxAdmin | null;
    onAccessChange: () => void;
}

export const ModalMailboxManageAccesses = ({ domainId, isOpen, onClose, mailbox, onAccessChange }: ModalMailboxManageAccessesProps) => {
    const { t } = useTranslation();
    const normalizedAccesses: NormalizedMailboxAccess[] = (mailbox?.accesses || []).map((access) => ({
        id: access.id,
        role: access.role,
        user: {
            ...access.user,
            email: access.user.email || access.user.id,
            full_name: access.user.full_name || "",
        },
    }));

    const {
        searchResults,
        isSearchLoading,
        accessRoleOptions,
        getAccessRoleOptions,
        getAccessRoleTopMessage,
        handleCreateAccesses,
        handleUpdateAccess,
        handleDeleteAccess,
        handleSearchUsers,
    } = useMailboxAccessManagement({
        mailboxId: mailbox?.id ?? "",
        domainId,
        accesses: normalizedAccesses,
        onAccessChange,
    });

    if (!mailbox) return null;

    return (
        <ShareModal<UserWithoutAbilities, UserWithoutAbilities, NormalizedMailboxAccess>
            modalTitle={t('Manage {{entity}} accesses', { entity: MailboxHelper.toString(mailbox) })}
            isOpen={isOpen}
            loading={isSearchLoading}
            canUpdate={true}
            onClose={onClose}
            invitationRoles={accessRoleOptions(false)}
            hideInvitations
            getAccessRoles={(access) => getAccessRoleOptions(access)}
            accessRoleTopMessage={(access) => getAccessRoleTopMessage(access)}
            onInviteUser={handleCreateAccesses}
            onUpdateAccess={handleUpdateAccess}
            onDeleteAccess={handleDeleteAccess}
            onSearchUsers={handleSearchUsers}
            searchUsersResult={searchResults}
            accesses={normalizedAccesses}
        />
    )
}
