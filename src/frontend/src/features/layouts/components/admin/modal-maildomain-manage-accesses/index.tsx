import { ShareModal } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMaildomainsUsersList, UserWithoutAbilities, useMaildomainsAccessesCreate, useMaildomainsAccessesDestroy, MailDomainAdmin, useMaildomainsAccessesList, MailDomainAccessRoleChoices, MaildomainAccessRead } from "@/features/api/gen";

type ModalMaildomainManageAccessesProps = {
    domain: MailDomainAdmin;
    isOpen: boolean;
    onClose: () => void;
}

export const ModalMaildomainManageAccesses = ({ domain, isOpen, onClose }: ModalMaildomainManageAccessesProps) => {
    const { t } = useTranslation();
    const [searchQuery, setSearchQuery] = useState("");
    const { data: {data: accesses = [] } = {}, isLoading: isLoadingAccesses, refetch: refetchAccesses } = useMaildomainsAccessesList(domain.id);
    const { mutate: createMaildomainAccess } = useMaildomainsAccessesCreate({ mutation: { onSuccess: () => refetchAccesses() } });
    const { mutate: deleteMaildomainAccess } = useMaildomainsAccessesDestroy({ mutation: { onSuccess: () => refetchAccesses() } });
    const { data: {data: searchUsers = [] } = {}, isLoading: isLoadingSearchUsers } = useMaildomainsUsersList(domain.id, { q: searchQuery });

    const getAccessUser = (user: UserWithoutAbilities) => {
        return {
            ...user,
            email: user.email || user.id,
            full_name: user.full_name || ""
        }
    };
    const searchResults = searchUsers.filter((result) => !(accesses).some(access => access.user.id === result.id)).map(getAccessUser) ?? [];
    const normalizedAccesses = (accesses || []).map(access => ({
        ...access,
        user: getAccessUser(access.user)
    }));


    const handleCreateAccesses = (users: UserWithoutAbilities[], role: string) => {
        const userIds = [...new Set(users.map((m) => m.id))];
        userIds.forEach((userId) => {
            createMaildomainAccess({
                maildomainPk: domain!.id,
                data: {
                    user: userId,
                    role: role as MailDomainAccessRoleChoices,
                }
            });
        });
    }

    const handleDeleteAccess = (access: MaildomainAccessRead) => {
        deleteMaildomainAccess({
            maildomainPk: domain!.id,
            id: access.id,
        });
    }


    const accessRoleOptions = () => Object.values(MailDomainAccessRoleChoices).map((role) => {
        return {
            label: t(`manage_accesses_modal.roles.${role}`),
            value: role,
        }
    });

    if (!domain) return null;

    return (
        <ShareModal<UserWithoutAbilities, UserWithoutAbilities, MaildomainAccessRead>
            modalTitle={t('manage_accesses_modal.title', { entity: domain.name })}
            isOpen={isOpen}
            loading={isLoadingAccesses || isLoadingSearchUsers}
            canUpdate={true}
            onClose={onClose}
            invitationRoles={accessRoleOptions()}
            getAccessRoles={() => accessRoleOptions()}
            onInviteUser={handleCreateAccesses}
            onDeleteAccess={handleDeleteAccess}
            searchUsersResult={searchResults}
            onSearchUsers={setSearchQuery}
            accesses={normalizedAccesses}
        />
    )
}
