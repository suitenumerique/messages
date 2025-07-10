import { ShareModal } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { MailboxAccessNestedUser, MailboxAccessRoleEnum, MailboxAdmin, useMailboxesAccessesCreate, useMailboxesAccessesDestroy, useMailboxesAccessesUpdate, useMaildomainsUsersList, User } from "@/features/api/gen";

type ModalMailboxManageAccessesProps = {
    domainId: string;
    isOpen: boolean;
    onClose: () => void;
    mailbox: MailboxAdmin | undefined;
    onAccessChange: () => void;
}

export const ModalMailboxManageAccesses = ({ domainId, isOpen, onClose, mailbox, onAccessChange }: ModalMailboxManageAccessesProps) => {
    const { t } = useTranslation();
    const [searchQuery, setSearchQuery] = useState("");
    const { mutate: createMailboxAccess } = useMailboxesAccessesCreate({ mutation: { onSuccess: onAccessChange } });
    const { mutate: updateMailboxAccess } = useMailboxesAccessesUpdate({ mutation: { onSuccess: onAccessChange } });
    const { mutate: deleteMailboxAccess } = useMailboxesAccessesDestroy({ mutation: { onSuccess: onAccessChange } });
    const mailbox_write_roles: MailboxAccessRoleEnum[] = [MailboxAccessRoleEnum.admin, MailboxAccessRoleEnum.editor];
    const hasOnlyOneEditor = (mailbox?.accesses || []).filter((a) => mailbox_write_roles.includes(a.role)).length === 1;
    const searchUsersQuery = useMaildomainsUsersList(domainId, { q: searchQuery });

    const getAccessUser = (user: User) => {
        return {
            ...user,
            email: user.email || user.id,
            full_name: user.full_name || user.short_name || ""
        }
    };
    const searchResults = searchUsersQuery.data?.data.filter((result) => !(mailbox?.accesses||[]).some(access => access.user.id === result.id)).map(getAccessUser) ?? [];
    const normalizedAccesses = (mailbox?.accesses || []).map(access => ({
        ...access,
        user: getAccessUser(access.user)
    }));


    const handleCreateAccesses = (users: User[], role: string) => {
        const userIds = [...new Set(users.map((m) => m.id))];
        userIds.forEach((userId) => {
            createMailboxAccess({
                mailboxId: mailbox!.id,
                data: {
                    user: userId,
                    role: role as MailboxAccessRoleEnum,
                }
            });
        });
    }
    const handleUpdateAccess = (access: MailboxAccessNestedUser, role: string) => {
        updateMailboxAccess({
            mailboxId: mailbox!.id,
            id: access.id,
            data: {
                user: access.user.id,
                role: role as MailboxAccessRoleEnum,
            }
        });
    }

    const handleDeleteAccess = (access: MailboxAccessNestedUser) => {
        deleteMailboxAccess({
            mailboxId: mailbox!.id,
            id: access.id,
        });
    }


    const accessRoleOptions = (isDisabled?: boolean) => Object.values(MailboxAccessRoleEnum).map((role) => {
        return {
            label: t(`manage_accesses_modal.roles.${role}`),
            value: role,
            isDisabled: isDisabled ?? (hasOnlyOneEditor && role !== MailboxAccessRoleEnum.editor),
        }
    });

    if (!mailbox) return null;

    return (
        <ShareModal<User, User, MailboxAccessNestedUser>
            modalTitle={t('manage_accesses_modal.title', { mailbox: mailbox.local_part + "@" + mailbox.domain_name })}
            isOpen={isOpen}
            loading={searchUsersQuery.isLoading}
            canUpdate={true}
            onClose={onClose}
            invitationRoles={accessRoleOptions(false)}
            getAccessRoles={() => accessRoleOptions()}
            onInviteUser={handleCreateAccesses}
            onUpdateAccess={handleUpdateAccess}
            onDeleteAccess={handleDeleteAccess}
            onSearchUsers={setSearchQuery}
            searchUsersResult={searchResults}
            accesses={normalizedAccesses}
        />
    )
}
