import { Button, Tooltip } from "@openfun/cunningham-react"
import { ShareModal } from "@gouvfr-lasuite/ui-kit"
import { useState } from "react";
import { ThreadAccessRoleEnum, ThreadAccessDetail, MailboxLight } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { useMailboxesSearchList, useThreadsAccessesCreate, useThreadsAccessesDestroy, useThreadsAccessesUpdate } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ThreadAccessesList } from "./thread-accesses-list";



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
    const { selectedMailbox, selectedThread, invalidateThreadMessages } = useMailboxContext();
    const { mutate: removeThreadAccess } = useThreadsAccessesDestroy({ mutation: { onSuccess: invalidateThreadMessages } });
    const { mutate: createThreadAccess } = useThreadsAccessesCreate({ mutation: { onSuccess: invalidateThreadMessages } });
    const { mutate: updateThreadAccess } = useThreadsAccessesUpdate({ mutation: { onSuccess: invalidateThreadMessages } });
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
    const normalizedAccesses = accesses.map((access) => ({ ...access, user: getAccessUser(access.mailbox) }));
    const hasOnlyOneEditor = accesses.filter((a) => a.role === ThreadAccessRoleEnum.editor).length === 1;
    const isEditor = accesses.some((a) => a.role === ThreadAccessRoleEnum.editor && a.mailbox.id === selectedMailbox!.id);

    const handleCreateAccesses = (mailboxes: MailboxLight[], role: string) => {
        const mailboxIds = [...new Set(mailboxes.map((m) => m.id))];
        mailboxIds.forEach((mailboxId) => {
            createThreadAccess({
                threadId: selectedThread!.id,
                data: {
                    thread: selectedThread!.id,
                    mailbox: mailboxId,
                    role: role as ThreadAccessRoleEnum,
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
                role: role as ThreadAccessRoleEnum,
            }
        });
    }

    const handleDeleteAccess = (access: ThreadAccessDetail) => {
        // TODO : Update Share Modal to hide the remove button if there is only one editor
        if (hasOnlyOneEditor && access.role === ThreadAccessRoleEnum.editor) {
            addToast(<ToasterItem type="error">
                <p>{t('thread_accesses_widget.last_editor_deletion_forbidden')}</p>
            </ToasterItem>, {
                toastId: "last-editor-deletion-forbidden",
                autoClose: 3000,
            });
            return;
        };
        removeThreadAccess({
            id: access.id,
            threadId: selectedThread!.id
        }, {
            onSuccess: () => {
                addToast(<ToasterItem>
                    <p>{t('thread_accesses_widget.access_removed')}</p>
                </ToasterItem>);
            }
        });
    }

    const accessRoleOptions = (isDisabled?: boolean) => Object.values(ThreadAccessRoleEnum).map((role) => {
        return {
            label: t(`roles.${role}`),
            value: role,
            isDisabled: isDisabled ?? (hasOnlyOneEditor && role !== ThreadAccessRoleEnum.editor),
        }
    });

    return (
        <>
            <Tooltip content={t('thread_accesses_widget.see_members')}>
            {accesses.length > 1 ? (
                <Button color="tertiary-text" size="small" className="thread-accesses-widget" onClick={() => setIsShareModalOpen(true)}>
                    <ThreadAccessesList accesses={accesses} />
                    <div className="thread-accesses-widget__item thread-accesses-widget__item--count">
                        {accesses.length}
                    </div>
                </Button>
            ) : (
                <Button color="tertiary-text" size="small" icon={<span className="material-icons">supervised_user_circle</span>} onClick={() => setIsShareModalOpen(true)}>
                    <p>{t('thread_accesses_widget.share_access')}</p>
                    </Button>
                )}
            </Tooltip>
            <ShareModal<MailboxLight, MailboxLight, ThreadAccessDetail>
                modalTitle={t('thread_accesses_widget.share_access')}
                isOpen={isShareModalOpen}
                loading={searchMailboxesQuery.isLoading}
                canUpdate={isEditor}
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
                    if (hasOnlyOneEditor && access.role === ThreadAccessRoleEnum.editor) {
                        return t('thread_accesses_widget.last_editor_role_top_message');
                    }
                }}
            />
        </>
    )
}
