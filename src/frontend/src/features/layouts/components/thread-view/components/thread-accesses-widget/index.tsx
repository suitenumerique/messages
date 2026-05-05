import { Button, Tooltip, useModals } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useQueryClient } from "@tanstack/react-query";
import { forwardRef, useImperativeHandle, useMemo, useState } from "react";
import {
    ThreadAccess,
    ThreadAccessRoleChoices,
    ThreadAccessDetail,
    MailboxLight,
    UserWithoutAbilities,
    getThreadsAccessesListQueryKey,
    threadsAccessesListResponse,
    useMailboxesSearchList,
    useThreadsAccessesCreate,
    useThreadsAccessesList,
    useThreadsAccessesUpdate,
} from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useIsSharedContext } from "@/hooks/use-is-shared-context";
import { useAssignedUsers } from "@/features/message/use-assigned-users";
import { useThreadAssignment } from "@/features/message/use-thread-assignment";
import { AssignedUsersSection, AccessUsersList, ShareModal } from "../share-modal-extensions";
import useDeleteThreadAccess from "@/features/message/use-delete-thread-access";

export type ThreadAccessesWidgetHandle = {
    open: () => void;
};

type ThreadAccessesWidgetProps = {
    accesses: readonly ThreadAccessDetail[];
};

/**
 * Display shape consumed by the ShareModal: `accesses` from Thread (nested
 * mailbox for rendering) joined with `users` from the `/accesses/` endpoint.
 * The join is required because `/accesses/` returns the mailbox as a flat FK
 * UUID — it's the authoritative source for `users`, but Thread.accesses
 * remains the source for mailbox display info.
 */
type EnrichedAccess = ThreadAccessDetail & {
    users: readonly UserWithoutAbilities[];
};

/**
 * Lists thread accesses and lets users manage sharing + per-user assignment.
 * Exposes an `open()` handle so the `AssigneesWidget` (rendered inside
 * `ThreadActionBar`) can reuse the exact same modal without duplicating state.
 *
 * The `ShareModal` from `@gouvfr-lasuite/ui-kit` is reused as-is for visual
 * consistency; assignment affordances are injected through its extension
 * points (`children` for the "assigned users" section, `accessRoleTopMessage`
 * returning a ReactNode for the per-mailbox user list).
 */
export const ThreadAccessesWidget = forwardRef<ThreadAccessesWidgetHandle, ThreadAccessesWidgetProps>(
    function ThreadAccessesWidget({ accesses }, ref) {
    const { t } = useTranslation();
    const [isShareModalOpen, setIsShareModalOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const {
        selectedMailbox,
        selectedThread,
        invalidateMailbox,
    } = useMailboxContext();
    const modals = useModals();
    const assignedUsers = useAssignedUsers();
    const {
        assignedUserIds,
        mutatingUserIds,
        markUserMutating,
        clearUserMutating,
        dispatchAssignEvent,
        unassignUser,
    } = useThreadAssignment();
    const queryClient = useQueryClient();

    useImperativeHandle(ref, () => ({
        open: () => setIsShareModalOpen(true),
    }), []);

    // Any mutation on thread accesses (add/update/remove member) also
    // impacts the thread messages (roles gate UI abilities), hence the
    // shared invalidation below.
    //
    // Per-mutation cache strategy on the `/accesses/` list:
    //   - update: targeted patch — the response carries the new row,
    //     applying it directly avoids a round-trip that would leave the
    //     select stuck on the old value between success and refetch.
    //   - create: full refetch — the response lacks the per-mailbox
    //     `users` list required by the modal.
    //   - delete: handled by `useDeleteThreadAccess`. No patch on this
    //     cache; the inner-join in `enrichedAccesses` drops the row as
    //     soon as `invalidateMailbox` refreshes the thread prop.
    const patchAccessesCache = (
        updater: (prev: ThreadAccess[]) => ThreadAccess[],
    ) => {
        if (!selectedThread?.id) return;
        queryClient.setQueryData<threadsAccessesListResponse>(
            getThreadsAccessesListQueryKey(selectedThread.id),
            (old) => (old ? { ...old, data: updater(old.data) } : old),
        );
    };

    const {
        deleteThreadAccess,
        isPending: isDeletePending,
        variables: deleteVariables,
    } = useDeleteThreadAccess();
    const createMutation = useThreadsAccessesCreate({
        mutation: {
            onSuccess: () => {
                invalidateMailbox();
                if (selectedThread?.id) {
                    queryClient.invalidateQueries({
                        queryKey: getThreadsAccessesListQueryKey(selectedThread.id),
                    });
                }
            },
        },
    });
    const updateMutation = useThreadsAccessesUpdate({
        mutation: {
            onSuccess: (data) => {
                invalidateMailbox();
                patchAccessesCache((prev) =>
                    prev.map((a) =>
                        a.id === data.data.id ? { ...a, role: data.data.role } : a,
                    ),
                );
            },
        },
    });
    // Per-row pending state: while a mutation is in flight for a given
    // access, the modal shows a spinner next to that row so the user
    // knows their click registered.
    const isAccessPending = (accessId: string) =>
        (updateMutation.isPending && updateMutation.variables?.id === accessId) ||
        (isDeletePending && deleteVariables?.id === accessId);
    const isMailboxPending = (mailboxId: string) =>
        createMutation.isPending &&
        createMutation.variables?.data.mailbox === mailboxId;

    const searchMailboxesQuery = useMailboxesSearchList(
        selectedMailbox?.id ?? "",
        { q: searchQuery },
        { query: { enabled: !!(selectedMailbox && searchQuery) } },
    );

    const canManageThreadAccess = useAbility(Abilities.CAN_MANAGE_THREAD_ACCESS, [selectedMailbox!, selectedThread!]);
    const isAssignmentContext = useIsSharedContext();

    const threadAccessesQuery = useThreadsAccessesList(
        selectedThread?.id ?? "",
        undefined,
        {
            query: {
                enabled:
                    !!selectedThread?.id && isShareModalOpen && canManageThreadAccess,
            },
        },
    );
    // Join Thread.accesses (nested mailbox, no users) with /accesses/
    // (flat mailbox FK + users per access). The /accesses/ endpoint is
    // the source of truth for `role` and `users`: it is patched
    // synchronously by `patchAccessesCache` on update/remove, so it
    // reflects the new state before the thread payload refetch
    // completes. The thread prop is the only source for the nested
    // mailbox object required by the modal — we keep it as the join
    // base. Viewers (who can't manage accesses) get no /accesses/
    // response and fall back to the prop with empty `users`.
    const accessesById = useMemo(
        () => new Map(accesses.map((access) => [access.id, access])),
        [accesses],
    );
    const enrichedAccesses: readonly EnrichedAccess[] = useMemo(() => {
        const fresh = threadAccessesQuery.data?.data;
        if (fresh) {
            return fresh
                .map((access) => {
                    const base = accessesById.get(access.id);
                    return base
                        ? { ...base, role: access.role, users: access.users ?? [] }
                        : null;
                })
                .filter((a): a is EnrichedAccess => a !== null);
        }
        return accesses.map((access) => ({ ...access, users: [] }));
    }, [accesses, accessesById, threadAccessesQuery.data?.data]);

    const hasOnlyOneEditor =
        enrichedAccesses.filter((a) => a.role === ThreadAccessRoleChoices.editor).length === 1;

    const getAccessUser = (mailbox: MailboxLight) => ({
        ...mailbox,
        full_name: mailbox.name,
    });

    const searchResults = searchMailboxesQuery.data?.data
        .filter((mailbox) => !enrichedAccesses.some((a) => a.mailbox.id === mailbox.id))
        .map(getAccessUser) ?? [];

    const normalizedAccesses = enrichedAccesses.map((access) => ({
        ...access,
        user: getAccessUser(access.mailbox),
        can_delete: canManageThreadAccess && enrichedAccesses.length > 1 && (!hasOnlyOneEditor || access.role !== ThreadAccessRoleChoices.editor),
    }));

    const accessRoleOptions = (isDisabled?: boolean) =>
        Object.values(ThreadAccessRoleChoices).map((role) => ({
            label: t(`thread_roles_${role}`, { ns: 'roles' }),
            value: role,
            isDisabled: isDisabled ?? (hasOnlyOneEditor && role !== ThreadAccessRoleChoices.editor),
        }));

    const handleCreateAccesses = (mailboxes: MailboxLight[], role: string) => {
        const mailboxIds = [...new Set(mailboxes.map((m) => m.id))];
        mailboxIds.forEach((mailboxId) => {
            createMutation.mutate({
                threadId: selectedThread!.id,
                data: {
                    thread: selectedThread!.id,
                    mailbox: mailboxId,
                    role: role as ThreadAccessRoleChoices,
                },
            });
        });
    };

    const handleUpdateAccess = (access: EnrichedAccess, role: string) => {
        updateMutation.mutate({
            id: access.id,
            threadId: selectedThread!.id,
            data: {
                thread: selectedThread!.id,
                mailbox: access.mailbox.id,
                role: role as ThreadAccessRoleChoices,
            },
        });
    };

    const handleDeleteAccess = async (access: EnrichedAccess) => {
        if (hasOnlyOneEditor && access.role === ThreadAccessRoleChoices.editor) {
            addToast(
                <ToasterItem type="error">
                    <p>{t('You cannot delete the last editor of this thread')}</p>
                </ToasterItem>,
                { toastId: "last-editor-deletion-forbidden", autoClose: 3000 },
            );
            return;
        }
        const isSelfRemoval = access.mailbox.id === selectedMailbox?.id;
        const decision = await modals.deleteConfirmationModal({
            title: isSelfRemoval ? t('Leave this thread?') : t('Remove access?'),
            children: isSelfRemoval
                ? t(
                    'You and all users with access to the mailbox "{{mailboxName}}" will no longer see this thread.',
                    { mailboxName: access.mailbox.email },
                )
                : t(
                    'All users with access to the mailbox "{{mailboxName}}" will no longer see this thread.',
                    { mailboxName: access.mailbox.email },
                ),
        });
        if (decision !== 'delete') return;
        deleteThreadAccess({
            accessId: access.id,
            accessMailboxId: access.mailbox.id,
            threadId: selectedThread!.id,
            onSuccess: () => {
                addToast(
                    <ToasterItem>
                        <p>{t('Thread access removed')}</p>
                    </ToasterItem>,
                );
                if (isSelfRemoval) {
                    setIsShareModalOpen(false);
                }
            },
        });
    };

    const handleAssignUser = async (user: UserWithoutAbilities, access: EnrichedAccess) => {
        if (access.role === ThreadAccessRoleChoices.viewer) {
            const decision = await modals.confirmationModal({
                title: <span className="c__modal__text--centered">{t('Grant editor access to the thread?')}</span>,
                children: (
                    <span className="c__modal__text--centered">
                        {t(
                            'The mailbox "{{mailbox}}" currently has read-only access on this thread. To assign {{user}} to it, edit permissions must be granted to this mailbox.',
                            { mailbox: access.mailbox.email, user: user.full_name || user.email || "" },
                        )}
                    </span>
                ),
            });
            if (decision !== 'yes') return;
            markUserMutating(user.id);
            try {
                await updateMutation.mutateAsync({
                    id: access.id,
                    threadId: selectedThread!.id,
                    data: {
                        thread: selectedThread!.id,
                        mailbox: access.mailbox.id,
                        role: ThreadAccessRoleChoices.editor,
                    },
                });
            } catch {
                clearUserMutating(user.id);
                return;
            }
        } else {
            markUserMutating(user.id);
        }
        dispatchAssignEvent(user, { onSettled: () => clearUserMutating(user.id) });
    };

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
            <ShareModal<MailboxLight, MailboxLight, EnrichedAccess>
                modalTitle={isAssignmentContext ? t('Share and assign the thread') : t('Share the thread')}
                isOpen={isShareModalOpen}
                loading={searchMailboxesQuery.isLoading || threadAccessesQuery.isLoading}
                canUpdate={canManageThreadAccess}
                onClose={() => setIsShareModalOpen(false)}
                invitationRoles={accessRoleOptions(false)}
                getAccessRoles={() => accessRoleOptions()}
                onInviteUser={handleCreateAccesses}
                onUpdateAccess={handleUpdateAccess}
                onDeleteAccess={enrichedAccesses.length > 1 ? handleDeleteAccess : undefined}
                onSearchUsers={setSearchQuery}
                searchPlaceholder={t('Search a mailbox to share this thread with')}
                searchGroupName={t('Search results')}
                searchUsersResult={searchResults}
                accesses={normalizedAccesses}
                allowInvitation={false}
                getAccessClassName={(access) => {
                    // The mailbox row is rendered by upstream ShareMemberItem
                    // so we can't wrap the avatar ourselves — CSS hooks into
                    // these classes descendant-style.
                    //   --shared:   non-identity mailbox (alias / group)
                    //               → square-rounded avatar to signal "team".
                    //   --assigned: single-user identity mailbox whose user
                    //               is assigned to the thread → brand ring.
                    const classes: string[] = [];
                    if (access.mailbox.is_identity === false) {
                        classes.push("share-modal-extensions__share-member-item--shared");
                    }
                    if (
                        isAssignmentContext &&
                        access.users?.length === 1 &&
                        access.mailbox.is_identity !== false &&
                        assignedUserIds.has(access.users[0].id)
                    ) {
                        classes.push("share-modal-extensions__share-member-item--assigned");
                    }
                    return classes.length ? classes.join(" ") : undefined;
                }}
                membersTitle={(members) => {
                    const sharedCount = members.filter(
                        (m) => (m.users?.length ?? 0) > 1 || m.mailbox.is_identity === false,
                    ).length;
                    const total = t('Shared between {{count}} mailboxes', {
                        count: members.length,
                        defaultValue_one: 'Shared between {{count}} mailbox',
                    });
                    if (sharedCount === 0) {
                        return total;
                    }
                    const shared = t('{{count}} of which are shared', {
                        count: sharedCount,
                        defaultValue: ', {{count}} of which are shared',
                        defaultValue_one: ', {{count}} of which is shared',
                    });
                    return `${total}${shared}`;
                }}
                accessRoleTopMessage={(access) => {
                    if (hasOnlyOneEditor && access.role === ThreadAccessRoleChoices.editor) {
                        return t('You are the last editor of this thread, you cannot therefore modify your access.');
                    }
                    return undefined;
                }}
                renderAccessFooter={(access) => (
                    <AccessUsersList
                        access={access}
                        assignedUserIds={assignedUserIds}
                        canAssign={canManageThreadAccess && isAssignmentContext}
                        mutatingUserIds={mutatingUserIds}
                        onAssign={handleAssignUser}
                        onUnassign={unassignUser}
                    />
                )}
                renderAccessRightExtras={(access) => {
                    // Inline spinner when a mutation is in flight on this
                    // specific access row (role change or removal) — the
                    // ShareModal's own select can't convey "pending" state.
                    if (isAccessPending(access.id) || isMailboxPending(access.mailbox.id)) {
                        return <Spinner size="sm" />;
                    }
                    // For single-user identity mailboxes, the mailbox row
                    // already shows the user's name and email — duplicating
                    // it in a one-line sub-list would be pure noise. Render
                    // the assignment toggle inline next to the role select
                    // instead. Multi-user or non-identity mailboxes fall
                    // through to AccessUsersList.
                    if (!isAssignmentContext) return null;
                    if (!access.users || access.users.length !== 1) return null;
                    if (access.mailbox.is_identity === false) return null;
                    const user = access.users[0];
                    if (!canManageThreadAccess) return null;
                    if (assignedUserIds.has(user.id)) {
                        return (
                            <Button
                                onClick={() => unassignUser(user.id)}
                                size="small"
                                color="error"
                                variant="tertiary"
                                className="share-modal-extensions__toggle"
                                disabled={mutatingUserIds.size > 0}
                            >
                                {t('Unassign')}
                            </Button>
                        );
                    }
                    const isMutating = mutatingUserIds.has(user.id);
                    return (
                        <Button
                            onClick={() => handleAssignUser(user, access)}
                            size="small"
                            variant="secondary"
                            className="share-modal-extensions__toggle"
                            disabled={mutatingUserIds.size > 0}
                            icon={isMutating ? <Spinner size="sm" /> : undefined}
                        >
                            {t('Assign')}
                        </Button>
                    );
                }}
                outsideSearchContent={(
                    <div className="share-modal-extensions__footer">
                        <Button onClick={() => setIsShareModalOpen(false)}>
                            {t('OK')}
                        </Button>
                    </div>
                )}
            >
                {isAssignmentContext && (
                    <AssignedUsersSection
                        assignedUsers={assignedUsers}
                        canUpdate={canManageThreadAccess}
                        mutatingUserIds={mutatingUserIds}
                        onUnassign={unassignUser}
                    />
                )}
            </ShareModal>
        </>
    );
});
