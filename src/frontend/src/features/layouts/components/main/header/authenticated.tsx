import { DropdownMenu, HeaderProps, Icon, IconType, useResponsive, UserMenu, VerticalSeparator } from "@gouvfr-lasuite/ui-kit";
import { Controls, GearRounded, Upload } from "@gouvfr-lasuite/ui-kit/icons";
import { Button, Tooltip, useCunningham } from "@gouvfr-lasuite/cunningham-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "@tanstack/react-router";
import { SearchInput } from "@/features/forms/components/search-input";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useFeatureFlag, FEATURE_KEYS } from "@/hooks/use-feature";
import { useAuth, logout } from "@/features/auth";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { LagaufreButton } from "@/features/ui/components/lagaufre";
import { SurveyButton } from "@/features/ui/components/feedback-button";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTaskStatus } from "@/hooks/use-task-status";
import { MessageTemplateTypeChoices, StatusEnum, useMailboxesMessageTemplatesList } from "@/features/api/gen";
import { CircularProgress } from "@/features/ui/components/circular-progress";
import { TaskImportCacheHelper } from "@/features/utils/task-import-cache";
import { useTheme } from "@/features/providers/theme";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { useModalStore } from "@/features/providers/modal-store";


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
          color="brand"
          variant="tertiary"
          icon={
            <Icon name={isPanelOpen ? "close" : "menu"} />
          }
        />
      </div>
      <div className="c__header__left">
        {leftIcon}
      </div>
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

const AutoreplyIndicator = () => {
  const { selectedMailbox } = useMailboxContext();
  const { openModal } = useModalStore();
  const { t } = useTranslation();

  const { data } = useMailboxesMessageTemplatesList(
    selectedMailbox?.id ?? "",
    { type: [MessageTemplateTypeChoices.autoreply] },
    {
      query: {
        enabled: !!selectedMailbox?.id,
        staleTime: Infinity,
      },
    },
  );

  const hasActiveAutoreply = useMemo(
    () => data?.data?.some((tpl) => tpl.is_active_autoreply) ?? false,
    [data],
  );

  if (!hasActiveAutoreply) return null;

  return (
    <Tooltip content={t("Auto-reply is active")}>
      <Button
        className="autoreply-indicator-button"
        color="brand"
        variant="tertiary"
        size="medium"
        icon={<Icon name="forward_to_inbox" />}
        aria-label={t("Auto-reply is active")}
        onClick={() => {
          if (selectedMailbox) {
            openModal(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "autoreplies" });
          }
        }}
      />
    </Tooltip>
  );
};

export const HeaderRight = () => {
  const { user } = useAuth();
  const { isDesktop } = useResponsive();
  const { themeConfig } = useTheme();

  return (
    <>
      <div className="flex-row flex-align-center">
        <AutoreplyIndicator />
        <SurveyButton iconOnly color="brand" variant="tertiary" />
        <ApplicationMenu />
        {isDesktop && <VerticalSeparator size="24px" withPadding={false} />}
        <LagaufreButton />
      </div>
      <UserMenu
        user={user ? {
          full_name: user.full_name ?? undefined,
          email: user.email || ""
        } : null}
        logout={logout}
        termOfServiceUrl={themeConfig.terms_of_service_url}
        actions={
          <div className="user-menu__footer-action">
            <LanguagePicker size="small" compact />
          </div>
        }
      />
    </>
  );
};

const ApplicationMenu = () => {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const { openModal } = useModalStore();
  const { selectedMailbox } = useMailboxContext();
  const canAccessDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);
  const canImportMessages = useAbility(Abilities.CAN_IMPORT_MESSAGES, selectedMailbox);
  const canManageMessageTemplates = useAbility(Abilities.CAN_MANAGE_MESSAGE_TEMPLATES, selectedMailbox);
  const isIntegrationsEnabled = useFeatureFlag(FEATURE_KEYS.MAILBOX_ADMIN_CHANNELS);
  const canManageIntegrations = canManageMessageTemplates && isIntegrationsEnabled;
  const canAdministrateSelectedMailbox = useAbility(Abilities.CAN_MANAGE_ACCESSES, selectedMailbox);
  const canOpenMailboxSettings = canAdministrateSelectedMailbox || canManageMessageTemplates || canManageIntegrations;
  const { t } = useTranslation();
  const navigate = useNavigate();
  const taskId = useMemo(() => {
    const taskImportCacheHelper = new TaskImportCacheHelper(selectedMailbox?.id);
    return taskImportCacheHelper.get();
  }, [isDropdownOpen, selectedMailbox?.id]);

  const taskStatus = useTaskStatus(taskId, { enabled: canImportMessages && isDropdownOpen });
  const hasOptions = canAccessDomainAdmin || canImportMessages || canOpenMailboxSettings;
  const importMessageOption = useMemo(() => {
    let label = t("Import messages");
    let icon = <Upload />;

    if (taskStatus) {
      if (taskStatus.state === StatusEnum.PROGRESS) {
        label = t("Importing messages...");
        if (taskStatus.loading || taskStatus.progress === null) icon = <CircularProgress loading />;
        else icon = <CircularProgress progress={taskStatus.progress} withLabel />;
      }
      if (taskStatus.state === StatusEnum.SUCCESS) {
        label = t("Imported messages");
        icon = <CircularProgress progress={100} />;
      }
      if (taskStatus.state === StatusEnum.FAILURE) {
        label = t("Import failed");
          icon = <Icon name="error" type={IconType.OUTLINED} style={{ color: "var(--c--contextuals--content--semantic--error--primary)" }} />;
      }
    }

    return {
      label,
      icon,
      callback: () => {
        window.location.hash = `#modal-message-importer`;
      },
      showSeparator: canAccessDomainAdmin
    }
  }, [t, taskStatus]);

  if (!hasOptions) {
    return (
      <Tooltip content={t("No action available for this mailbox")}>
        <Button
          disabled
          onClick={(e) => e.preventDefault()}
          icon={<GearRounded />}
          aria-label={t("More options (none available for this mailbox)")}
          color="neutral"
          variant="tertiary"
        />
      </Tooltip>
    );
  }

  return (
    <>
    <DropdownMenu
          isOpen={isDropdownOpen}
          onOpenChange={setIsDropdownOpen}
          options={[
              ...(canOpenMailboxSettings ? [{
                label: t("All settings"),
                icon: <Controls size="medium"  />,
                callback: () => openModal(MODAL_MAILBOX_SETTINGS_ID),
                showSeparator: canAccessDomainAdmin && !canImportMessages
              }] : []),
              ...(canImportMessages ? [importMessageOption] : []),
              ...(canAccessDomainAdmin ? [{
                label: t("Domain admin"),
                icon: <Icon name="domain" style={{ fontSize: 24 }} />,
                callback: () => navigate({ to: "/domain" }),
              }] : []),
          ]}
      >
      <Button
          onClick={() => setIsDropdownOpen(true)}
          icon={<GearRounded />}
          aria-label={t("More options")}
          color="brand"
          variant="tertiary"
      />
      </DropdownMenu>
    </>
  )
}
