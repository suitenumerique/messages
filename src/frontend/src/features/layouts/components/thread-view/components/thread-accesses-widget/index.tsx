import { Button, Tooltip, useModals } from "@gouvfr-lasuite/cunningham-react"
import { Icon, IconType, ShareModal } from "@gouvfr-lasuite/ui-kit"
import { useState } from "react";
import { ThreadAccessRoleChoices, ThreadAccessDetail, MailboxLight } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { useMailboxesSearchList, useThreadsAccessesCreate, useThreadsAccessesUpdate } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import useAbility, { Abilities } from "@/hooks/use-ability";
import useDeleteThreadAccess from "@/features/message/use-delete-thread-access";



type ThreadAccessesWidgetProps = {
    accesses: readonly ThreadAccessDetail[];
}

/**
 * A Component which list all thread accesses and allow to manage them.
 * This feature is still under development and requires several improvements :
 * - Prevent deletion if there is only one editor
 * - Ask user confirmation before downgrading its access that remove its write right
 * - In the ShareModal, identify the authenticated user (suffix the name with (You))
 *
 * To achieve those developments, the `ui-kit` ShareModel must be improved.
 */
export const ThreadAccessesWidget = ({ accesses }: ThreadAccessesWidgetProps) => {
    const { t } = useTranslation();
    const [isShareModalOpen, setIsShareModalOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const { selectedMailbox, selectedThread, invalidateMailbox } = useMailboxContext();
    const modals = useModals();
    const { deleteThreadAccess } = useDeleteThreadAccess();
    const { mutate: createThreadAccess } = useThreadsAccessesCreate({ mutation: { onSuccess: () => invalidateMailbox() } });
    const { mutate: updateThreadAccess } = useThreadsAccessesUpdate({ mutation: { onSuccess: () => invalidateMailbox() } });
    const searchMailboxesQuery = useMailboxesSearchList(selectedMailbox?.id ?? "", {
        q: searchQuery,
    }, {
        query: {
            enabled: !!(selectedMailbox && searchQuery),
        }
    });

    const getAccessUser = (mailbox: MailboxLight) => ({
        ...mailbox,
        full_name: mailbox.name
    });

    const searchResults = searchMailboxesQuery.data?.data.filter((mailbox) => !accesses.some(a => a.mailbox.id === mailbox.id)).map(getAccessUser) ?? [];
    const hasOnlyOneEditor = accesses.filter((a) => a.role === ThreadAccessRoleChoices.editor).length === 1;
    const canManageThreadAccess = useAbility(Abilities.CAN_MANAGE_THREAD_ACCESS, [selectedMailbox!, selectedThread!]);
    const normalizedAccesses = accesses.map((access) => ({
        ...access,
        user: getAccessUser(access.mailbox),
        can_delete: canManageThreadAccess && accesses.length > 1 && (!hasOnlyOneEditor || access.role !== ThreadAccessRoleChoices.editor),
    }));

    const handleCreateAccesses = (mailboxes: MailboxLight[], role: string) => {
        const mailboxIds = [...new Set(mailboxes.map((m) => m.id))];
        mailboxIds.forEach((mailboxId) => {
            createThreadAccess({
                threadId: selectedThread!.id,
                data: {
                    thread: selectedThread!.id,
                    mailbox: mailboxId,
                    role: role as ThreadAccessRoleChoices,
                }
            });
        });
    }

    const handleUpdateAccess = (access: ThreadAccessDetail, role: string) => {
        updateThreadAccess({
            id: access.id,
            threadId: selectedThread!.id,
            data: {
                thread: selectedThread!.id,
                mailbox: access.mailbox.id,
                role: role as ThreadAccessRoleChoices,
            }
        });
    }

    const handleDeleteAccess = async (access: ThreadAccessDetail) => {
        // TODO : Update Share Modal to hide the remove button if there is only one editor
        if (hasOnlyOneEditor && access.role === ThreadAccessRoleChoices.editor) {
            addToast(<ToasterItem type="error">
                <p>{t('You cannot delete the last editor of this thread')}</p>
            </ToasterItem>, {
                toastId: "last-editor-deletion-forbidden",
                autoClose: 3000,
            });
            return;
        };
        const isSelfRemoval = access.mailbox.id === selectedMailbox?.id;
        const decision = await modals.deleteConfirmationModal({
            title: isSelfRemoval ? t('Leave this thread?') : t('Remove access?'),
            children: isSelfRemoval
                ? t(
                    'You and all users with access to the mailbox "{{mailboxName}}" will no longer see this thread.',
                    { mailboxName: access.mailbox.email }
                )
                : t(
                    'All users with access to the mailbox "{{mailboxName}}" will no longer see this thread.',
                    { mailboxName: access.mailbox.email }
                ),
        });
        if (decision !== 'delete') return;
        deleteThreadAccess({
            accessId: access.id,
            accessMailboxId: access.mailbox.id,
            threadId: selectedThread!.id,
            onSuccess: () => {
                addToast(<ToasterItem>
                    <p>{t('Thread access removed')}</p>
                </ToasterItem>);
                if (isSelfRemoval) {
                    setIsShareModalOpen(false);
                }
            },
        });
    }

    const accessRoleOptions = (isDisabled?: boolean) => Object.values(ThreadAccessRoleChoices).map((role) => {
        return {
            label: t(`thread_roles_${role}`, { ns: 'roles' }),
            value: role,
            isDisabled: isDisabled ?? (hasOnlyOneEditor && role !== ThreadAccessRoleChoices.editor),
        }
    });

    return (
        <>
            <Tooltip content={t('See members of this thread ({{count}} members)', { count: accesses.length })}>
                <Button
                    variant="tertiary"
                    size="nano"
                    aria-label={t('See members of this thread ({{count}} members)', { count: accesses.length })}
                    className="thread-accesses-widget"
                    onClick={() => setIsShareModalOpen(true)}
                    icon={<Icon name="group" type={IconType.FILLED} />}
                >
                    {accesses.length}
                </Button>
            </Tooltip>
            <ShareModal<MailboxLight, MailboxLight, ThreadAccessDetail>
                modalTitle={t('Share access')}
                isOpen={isShareModalOpen}
                loading={searchMailboxesQuery.isLoading}
                canUpdate={canManageThreadAccess}
                onClose={() => setIsShareModalOpen(false)}
                invitationRoles={accessRoleOptions(false)}
                getAccessRoles={() => accessRoleOptions()}
                onInviteUser={handleCreateAccesses}
                onUpdateAccess={handleUpdateAccess}
                onDeleteAccess={accesses.length > 1 ? handleDeleteAccess : undefined}
                onSearchUsers={setSearchQuery}
                searchUsersResult={searchResults}
                accesses={normalizedAccesses}
                accessRoleTopMessage={(access) => {
                    if (hasOnlyOneEditor && access.role === ThreadAccessRoleChoices.editor) {
                        return t('You are the last editor of this thread, you cannot therefore modify your access.');
                    }
                }}
            />
        </>
    )
}
