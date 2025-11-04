import { DropdownMenu, HeaderProps, Icon, IconType, useResponsive, UserMenu, VerticalSeparator } from "@gouvfr-lasuite/ui-kit";
import { Button, useCunningham } from "@openfun/cunningham-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { SearchInput } from "@/features/forms/components/search-input";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useAuth, logout } from "@/features/auth";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { LagaufreButton } from "@/features/ui/components/lagaufre";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useImportTaskStatus } from "@/hooks/use-import-task";
import { StatusEnum } from "@/features/api/gen";
import { CircularProgress } from "@/features/ui/components/circular-progress";
import { TaskImportCacheHelper } from "@/features/utils/task-import-cache";


type AuthenticatedHeaderProps = HeaderProps & {
  hideSearch?: boolean;
}

export const AuthenticatedHeader = ({
  leftIcon,
  onTogglePanel,
  isPanelOpen,
  hideSearch = false,
}: AuthenticatedHeaderProps) => {
  const { t } = useCunningham();
  const { isDesktop } = useResponsive();

  return (
    <div className="c__header">
      <div className="c__header__toggle-menu">
        <Button
          size="medium"
          onClick={onTogglePanel}
          aria-label={isPanelOpen ? t("Close the menu") : t("Open the menu")}
          color="tertiary-text"
          icon={
            <span className="material-icons clr-primary-800">
              {isPanelOpen ? "close" : "menu"}
            </span>
          }
        />
      </div>
      <div className="c__header__left">{leftIcon}</div>
      <div className="c__header__center">
        {!hideSearch && <SearchInput />}
      </div>
      {isDesktop && (
        <div className="c__header__right">
          <HeaderRight />
        </div>
      )}
    </div>
  );
};

export const HeaderRight = () => {
  const { user } = useAuth();
  const { isDesktop } = useResponsive();

  return (
    <>
      <ApplicationMenu />
      {isDesktop && <VerticalSeparator size="24px" />}
      <LagaufreButton />
      <UserMenu
        user={user ? {
          full_name: user.full_name ?? undefined,
          email: user.email || ""
        } : null}
        logout={logout}
        footerAction={
          <LanguagePicker size="small" color="secondary" />
        }
      />
    </>
  );
};

const ApplicationMenu = () => {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const { selectedMailbox } = useMailboxContext();
  const canAccessDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);
  const canImportMessages = useAbility(Abilities.CAN_IMPORT_MESSAGES, selectedMailbox);
  const canManageMessageTemplates = useAbility(Abilities.CAN_MANAGE_MESSAGE_TEMPLATES, selectedMailbox);
  const { t } = useTranslation();
  const router = useRouter();
  const taskId = useMemo(() => {
    const taskImportCacheHelper = new TaskImportCacheHelper(selectedMailbox?.id);
    return taskImportCacheHelper.get();
  }, [isDropdownOpen, selectedMailbox?.id]);

  const taskStatus = useImportTaskStatus(taskId, { enabled: canImportMessages && isDropdownOpen });
  const importMessageOption = useMemo(() => {
    let label = t("Import messages");
    let icon = <Icon name="archive" type={IconType.OUTLINED} />;

    if (taskStatus) {
      if (taskStatus.state === StatusEnum.PROGRESS) {
        label = t("Importing messages...");
        if (taskStatus.loading) icon = <CircularProgress loading />;
        else icon = <CircularProgress progress={taskStatus.progress} withLabel />;
      }
      if (taskStatus.state === StatusEnum.SUCCESS) {
        label = t("Imported messages");
        icon = <CircularProgress progress={100} />;
      }
      if (taskStatus.state === StatusEnum.FAILURE) {
        label = t("Import failed");
        icon = <Icon name="error" type={IconType.OUTLINED} />;
      }
    }

    return {
      label,
      icon,
      callback: () => {
        window.location.hash = `#modal-message-importer`;
      }
    }
  }, [taskStatus]);

  return (
    <DropdownMenu
          isOpen={isDropdownOpen}
          onOpenChange={setIsDropdownOpen}
          options={[
              ...(canAccessDomainAdmin ? [{
                label: t("Domain admin"),
                icon: <Icon name="domain" />,
                callback: () => router.push("/domain"),
              }] : []),
              ...(canImportMessages ? [importMessageOption] : []),
              ...(canManageMessageTemplates ? [{
                label: t("Message templates"),
                icon: <Icon name="description" />,
                callback: () => {
                    if (selectedMailbox) {
                        router.push(`/mailbox/${selectedMailbox.id}/message-templates`);
                    }
                }
            }] : []),
          ]}
      >
      <Button
          onClick={() => setIsDropdownOpen(true)}
          icon={<Icon name="settings" type={IconType.OUTLINED} />}
          aria-label={t("More options")}
          color="tertiary-text"
      />
      </DropdownMenu>
  )
}
