import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, ShareMemberItem, ShareModal } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Mailbox,
  UserWithoutAbilities,
  useMailboxesAccessesList,
} from "@/features/api/gen";
import { useAuth } from "@/features/auth";
import { useMailboxContext } from "@/features/providers/mailbox";
import {
  NormalizedMailboxAccess,
  useMailboxAccessManagement,
} from "@/hooks/use-mailbox-access-management";
import { ResourceSectionHeader } from "../resource-section-header";

type MailboxSettingsAccessTabProps = {
  mailbox: Mailbox;
};

export const MailboxSettingsAccessTab = ({
  mailbox,
}: MailboxSettingsAccessTabProps) => {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { refetchMailboxes } = useMailboxContext();
  const [isAddMemberOpen, setIsAddMemberOpen] = useState(false);

  // Only mailbox admins reach this tab (the settings modal filters on
  // `manage_accesses`), so the accesses list is always fetched. `noGlobalError`
  // guards the transient 403 when an admin removes their own access from here.
  const accessesQuery = useMailboxesAccessesList(mailbox.id, undefined, {
    query: { meta: { noGlobalError: true } },
  });
  const accesses: NormalizedMailboxAccess[] = (
    accessesQuery.data?.data.results ?? []
  ).map((access) => ({
    id: access.id,
    role: access.role,
    user: {
      ...access.user_details,
      email: access.user_details.email || access.user_details.id,
      full_name: access.user_details.full_name || "",
    },
  }));

  const {
    searchResults,
    isSearchLoading,
    accessRoleOptions,
    canDeleteAccess,
    getAccessRoleOptions,
    getAccessRoleTopMessage,
    handleCreateAccesses,
    handleUpdateAccess,
    handleDeleteAccess,
    handleSearchUsers,
  } = useMailboxAccessManagement({
    mailboxId: mailbox.id,
    domainId: mailbox.domain_id,
    accesses,
    onAccessChange: () => {
      accessesQuery.refetch();
    },
    ownerEmail: mailbox.email,
    currentUserId: user?.id,
    confirmBeforeDelete: true,
    onSelfAccessChange: () => {
      refetchMailboxes();
    },
  });

  return (
    <div className="mailbox-settings__tab mailbox-settings__access">
      <section className="mailbox-settings__section">
        <ResourceSectionHeader
          label={t("Address shared between {{count}} members", {
            count: accesses.length,
          })}
          action={
            <Button
              size="nano"
              icon={<Icon name="add" />}
              onClick={() => setIsAddMemberOpen(true)}
            >
              {t("Add a member")}
            </Button>
          }
        />
        <div className="mailbox-settings__accesses-list">
          {accesses.map((access) => (
            <ShareMemberItem<UserWithoutAbilities, NormalizedMailboxAccess>
              key={access.id}
              accessData={access}
              accessRoleKey="role"
              roles={getAccessRoleOptions(access)}
              canUpdate
              roleTopMessage={getAccessRoleTopMessage(access)}
              updateRole={handleUpdateAccess}
              deleteAccess={
                canDeleteAccess(access) ? handleDeleteAccess : undefined
              }
            />
          ))}
        </div>
      </section>

      <ShareModal<UserWithoutAbilities, UserWithoutAbilities, NormalizedMailboxAccess>
        modalTitle={t("Share the mailbox")}
        isOpen={isAddMemberOpen}
        onClose={() => setIsAddMemberOpen(false)}
        loading={isSearchLoading}
        canUpdate
        hideMembers
        invitationRoles={accessRoleOptions(false)}
        onInviteUser={(users, role) => {
          handleCreateAccesses(users, role);
          setIsAddMemberOpen(false);
        }}
        onSearchUsers={handleSearchUsers}
        searchUsersResult={searchResults}
      />
    </div>
  );
};
