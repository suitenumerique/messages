import { useModals } from "@gouvfr-lasuite/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  MailboxRoleChoices,
  UserWithoutAbilities,
  useMailboxesAccessesCreate,
  useMailboxesAccessesDestroy,
  useMailboxesAccessesUpdate,
  useUsersList,
} from "@/features/api/gen";

/**
 * A mailbox access normalized to the minimal shape the management UI needs,
 * regardless of the endpoint it came from (admin nested mailbox or the
 * regular `/mailboxes/{id}/accesses/` list).
 */
export type NormalizedMailboxAccess = {
  id: string;
  role: MailboxRoleChoices;
  user: UserWithoutAbilities & { email: string; full_name: string };
};

type UseMailboxAccessManagementParams = {
  mailboxId: string;
  /** Domain UUID, used to scope the user search to the mailbox domain. */
  domainId: string;
  accesses: NormalizedMailboxAccess[];
  /** Called after a successful create/update/delete so the caller can refetch. */
  onAccessChange: () => void;
  /**
   * Email of the mailbox "owner". When set (mailbox settings only), the access
   * whose user email matches it is protected: its role cannot be changed and it
   * cannot be deleted. A domain admin keeps full control because that modal does
   * not pass this option.
   */
  ownerEmail?: string;
  /** Id of the currently authenticated user, used to detect self-changes. */
  currentUserId?: string;
  /** When true, a confirmation modal is shown before deleting an access. */
  confirmBeforeDelete?: boolean;
  /**
   * Called instead of `onAccessChange` when the current user changes or removes
   * their own access: refetching the (admin-only) accesses list would 403, so the
   * caller refreshes the mailboxes list and lets abilities drive the UI instead.
   */
  onSelfAccessChange?: () => void;
};

/**
 * Shared logic to manage the accesses of a single mailbox: user search scoped
 * to the domain, role options, the "last admin" / "owner" guards and the
 * create/update/delete handlers. Consumed both by the domain-admin modal and the
 * self-service mailbox settings tab so the behaviour stays in a single place.
 */
export const useMailboxAccessManagement = ({
  mailboxId,
  domainId,
  accesses,
  onAccessChange,
  ownerEmail,
  currentUserId,
  confirmBeforeDelete,
  onSelfAccessChange,
}: UseMailboxAccessManagementParams) => {
  const { t } = useTranslation();
  const modals = useModals();
  const [searchQuery, setSearchQuery] = useState("");

  const { mutate: createMailboxAccess } = useMailboxesAccessesCreate({
    mutation: { onSuccess: onAccessChange },
  });
  // Update/delete resolve their onSuccess per call so self-changes can route to
  // `onSelfAccessChange` instead of a refetch that would 403 once the user is no
  // longer an admin.
  const { mutate: updateMailboxAccess } = useMailboxesAccessesUpdate();
  const { mutate: deleteMailboxAccess } = useMailboxesAccessesDestroy();

  const hasOnlyOneAdmin =
    accesses.filter((access) => access.role === MailboxRoleChoices.admin)
      .length === 1;

  const searchUsersQuery = useUsersList(
    { maildomain_pk: domainId, q: searchQuery },
    { query: { enabled: !!searchQuery.length } },
  );

  const getAccessUser = (user: UserWithoutAbilities) => ({
    ...user,
    email: user.email || user.id,
    full_name: user.full_name || "",
  });

  const searchResults =
    searchUsersQuery.data?.data
      .filter((result) => !accesses.some((access) => access.user.id === result.id))
      .map(getAccessUser) ?? [];

  const accessRoleOptions = (isDisabled?: boolean) =>
    Object.values(MailboxRoleChoices).map((role) => ({
      label: t(`mailbox_roles_${role}`, { ns: "roles" }),
      value: role,
      isDisabled,
    }));

  const isLastAdmin = (role: string) =>
    hasOnlyOneAdmin && role === MailboxRoleChoices.admin;

  const isOwner = (access: NormalizedMailboxAccess) =>
    ownerEmail != null && access.user.email === ownerEmail;

  /** The role of a locked access cannot be changed (last admin or owner). */
  const isRoleLocked = (access: NormalizedMailboxAccess) =>
    isLastAdmin(access.role) || isOwner(access);

  const canDeleteAccess = (access: NormalizedMailboxAccess) =>
    !isLastAdmin(access.role) && !isOwner(access);

  const getAccessRoleOptions = (access: NormalizedMailboxAccess) =>
    accessRoleOptions(isRoleLocked(access));

  const getAccessRoleTopMessage = (access: NormalizedMailboxAccess) => {
    if (isOwner(access)) {
      return t("This is the mailbox owner, its access cannot be modified.");
    }
    if (isLastAdmin(access.role)) {
      return t(
        "This is the only admin of this mailbox, you cannot therefore modify its access.",
      );
    }
    return undefined;
  };

  const handleCreateAccesses = (users: { id: string }[], role: string) => {
    const userIds = [...new Set(users.map((user) => user.id))];
    userIds.forEach((userId) => {
      createMailboxAccess({
        mailboxId,
        data: { user: userId, role: role as MailboxRoleChoices },
      });
    });
  };

  const handleUpdateAccess = (access: NormalizedMailboxAccess, role: string) => {
    if (isRoleLocked(access)) return;
    const isSelfChange = currentUserId != null && access.user.id === currentUserId;
    updateMailboxAccess(
      {
        mailboxId,
        id: access.id,
        data: { user: access.user.id, role: role as MailboxRoleChoices },
      },
      {
        onSuccess: () =>
          isSelfChange ? onSelfAccessChange?.() : onAccessChange(),
      },
    );
  };

  const handleDeleteAccess = async (access: NormalizedMailboxAccess) => {
    if (!canDeleteAccess(access)) return;
    const isSelfChange = currentUserId != null && access.user.id === currentUserId;

    if (confirmBeforeDelete) {
      const decision = await modals.deleteConfirmationModal({
        title: isSelfChange ? t("Leave this mailbox?") : t("Remove this access?"),
        children: isSelfChange
          ? t('You will no longer have access to the mailbox "{{mailboxName}}".', {
              mailboxName: ownerEmail,
            })
          : t('{{name}} will no longer have access to the mailbox "{{mailboxName}}".', {
              name: access.user.full_name || access.user.email,
              mailboxName: ownerEmail,
            }),
      });
      if (decision !== "delete") return;
    }

    deleteMailboxAccess(
      { mailboxId, id: access.id },
      {
        onSuccess: () =>
          isSelfChange ? onSelfAccessChange?.() : onAccessChange(),
      },
    );
  };

  const handleSearchUsers = (query: string) => {
    const trimmed = query.trim();
    if (trimmed.length >= 3) {
      setSearchQuery(trimmed);
    } else if (searchQuery !== "") {
      setSearchQuery("");
    }
  };

  return {
    searchResults,
    isSearchLoading: searchUsersQuery.isLoading,
    hasOnlyOneAdmin,
    isLastAdmin,
    isOwner,
    isRoleLocked,
    canDeleteAccess,
    accessRoleOptions,
    getAccessRoleOptions,
    getAccessRoleTopMessage,
    handleCreateAccesses,
    handleUpdateAccess,
    handleDeleteAccess,
    handleSearchUsers,
  };
};
