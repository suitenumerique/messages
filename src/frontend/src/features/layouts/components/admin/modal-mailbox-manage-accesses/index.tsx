import { ShareModal } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { MailboxAccessNestedUser, MailboxRoleChoices, MailboxAdmin, useMailboxesAccessesCreate, useMailboxesAccessesDestroy, useMailboxesAccessesUpdate, UserWithoutAbilities, useUsersList } from "@/features/api/gen";
import MailboxHelper from "@/features/utils/mailbox-helper";

type ModalMailboxManageAccessesProps = {
    domainId: string;
    isOpen: boolean;
    onClose: () => void;
    mailbox: MailboxAdmin | null;
    onAccessChange: () => void;
}

export const ModalMailboxManageAccesses = ({ domainId, isOpen, onClose, mailbox, onAccessChange }: ModalMailboxManageAccessesProps) => {
    const { t } = useTranslation();
    const [searchQuery, setSearchQuery] = useState("");
    const { mutate: createMailboxAccess } = useMailboxesAccessesCreate({ mutation: { onSuccess: onAccessChange } });
    const { mutate: updateMailboxAccess } = useMailboxesAccessesUpdate({ mutation: { onSuccess: onAccessChange } });
    const { mutate: deleteMailboxAccess } = useMailboxesAccessesDestroy({ mutation: { onSuccess: onAccessChange } });
    const hasOnlyOneAdmin = (mailbox?.accesses || []).filter((a) => a.role === MailboxRoleChoices.admin).length === 1;
    const searchUsersQuery = useUsersList({ maildomain_pk: domainId, q: searchQuery }, { query: { enabled: !!searchQuery.length } });

    const getAccessUser = (user: UserWithoutAbilities) => {
        return {
            ...user,
            email: user.email || user.id,
            full_name: user.full_name || ""
        }
    };
    const searchResults = searchUsersQuery.data?.data.filter((result) => !(mailbox?.accesses||[]).some(access => access.user.id === result.id)).map(getAccessUser) ?? [];
    const normalizedAccesses = (mailbox?.accesses || []).map(access => ({
        ...access,
        user: getAccessUser(access.user),
    }));


    const handleCreateAccesses = (users: UserWithoutAbilities[], role: string) => {
        const userIds = [...new Set(users.map((m) => m.id))];
        userIds.forEach((userId) => {
            createMailboxAccess({
                mailboxId: mailbox!.id,
                data: {
                    user: userId,
                    role: role as MailboxRoleChoices,
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
                role: role as MailboxRoleChoices,
            }
        });
    }

    const handleDeleteAccess = (access: MailboxAccessNestedUser) => {
        if (hasOnlyOneAdmin && access.role === MailboxRoleChoices.admin) return;
        deleteMailboxAccess({
            mailboxId: mailbox!.id,
            id: access.id,
        });
    }


    const accessRoleOptions = (isDisabled?: boolean) => Object.values(MailboxRoleChoices).map((role) => {
        return {
            label: t(`mailbox_roles_${role}`, { ns: 'roles' }),
            value: role,
            isDisabled: isDisabled,
        }
    });

    const handleSearchUsers = (query: string) => {
        const q = query.trim();
        if (q.length >= 3) {
            setSearchQuery(q);
        } else if (searchQuery != "") {
            setSearchQuery("");
        }
    }

    if (!mailbox) return null;

    return (
        <ShareModal<UserWithoutAbilities, UserWithoutAbilities, MailboxAccessNestedUser>
            modalTitle={t('Manage {{entity}} accesses', { entity: MailboxHelper.toString(mailbox) })}
            isOpen={isOpen}
            loading={searchUsersQuery.isLoading}
            canUpdate={true}
            onClose={onClose}
            invitationRoles={accessRoleOptions(false)}
            hideInvitations
            getAccessRoles={(access) => accessRoleOptions(hasOnlyOneAdmin && access.role === MailboxRoleChoices.admin)}
            accessRoleTopMessage={(access) => {
                if (hasOnlyOneAdmin && access.role === MailboxRoleChoices.admin) {
                    return t('This is the only admin of this mailbox, you cannot therefore modify its access.');
                }
            }}
            onInviteUser={handleCreateAccesses}
            onUpdateAccess={handleUpdateAccess}
            onDeleteAccess={handleDeleteAccess}
            onSearchUsers={handleSearchUsers}
            searchUsersResult={searchResults}
            accesses={normalizedAccesses}
        />
    )
}
