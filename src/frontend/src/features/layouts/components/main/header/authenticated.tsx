import { DropdownMenu, HeaderProps, Icon, useResponsive, UserMenu, VerticalSeparator } from "@gouvfr-lasuite/ui-kit";
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
import { ImportRun, MessageTemplateTypeChoices, useMailboxesImportsList, useMailboxesMessageTemplatesList } from "@/features/api/gen";
import { isTerminal } from "@/hooks/import-status";
import { CircularProgress } from "@/features/ui/components/circular-progress";
import { useTheme } from "@/features/providers/theme";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { useOpenImporter } from "@/features/layouts/components/mailbox-settings/imports-view/use-open-importer";
import { MODAL_NOTIFICATIONS_ID } from "@/features/layouts/components/notifications-settings/modal-notifications";
import { useModalStore } from "@/features/providers/modal-store";
import { useConfig } from "@/features/providers/config";


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

/**
 * Same strategy as the auto-reply indicator: while at least one import run is in
 * progress for the selected mailbox, show a live progress button in the header
 * that opens the mailbox settings modal on the Imports tab. Several imports can
 * run at once, so the badge shows their combined (total-weighted) progress and
 * the tab lists them individually.
 */
const ImportIndicator = () => {
  const { selectedMailbox } = useMailboxContext();
  const { openModal } = useModalStore();
  const { t } = useTranslation();
  const canImportMessages = useAbility(Abilities.CAN_IMPORT_MESSAGES, selectedMailbox);

  const { data } = useMailboxesImportsList(selectedMailbox?.id ?? "", {
    query: {
      enabled: !!selectedMailbox?.id && canImportMessages,
      // Poll while a run is live so the progress stays fresh; when idle the
      // importer modal's invalidations wake this query up on a new run.
      refetchInterval: (query) => {
        const rows = (query.state.data?.data as ImportRun[] | undefined) ?? [];
        return rows.some((r) => r.is_active && !isTerminal(r.status)) ? 60000 : false;
      },
    },
    // Background status poll: let foreground requests win the wire.
    request: { priority: "low" },
  });

  const activeRuns = useMemo(
    () =>
      ((data?.data as ImportRun[] | undefined) ?? []).filter(
        (r) => r.is_active && !isTerminal(r.status),
      ),
    [data],
  );

  if (!selectedMailbox || activeRuns.length === 0) return null;

  // Weighted average across the runs that already know their total, so a small
  // run can't dominate the badge; indeterminate until at least one knows its
  // total. Capped below 100 — the button disappears on completion.
  const withTotal = activeRuns.filter((r) => (r.total_messages ?? 0) > 0);
  const totalMessages = withTotal.reduce(
    (sum, r) => sum + (r.total_messages ?? 0),
    0,
  );
  const progress = withTotal.length
    ? Math.min(
        99,
        Math.round(
          withTotal.reduce(
            (sum, r) => sum + (r.progress ?? 0) * (r.total_messages ?? 0),
            0,
          ) / totalMessages,
        ),
      )
    : null;

  return (
    <Tooltip content={t("Import in progress")}>
      <Button
        className="import-indicator-button"
        color="brand"
        variant="tertiary"
        size="medium"
        icon={
          progress === null ? (
            <CircularProgress loading />
          ) : (
            <CircularProgress progress={progress} withLabel />
          )
        }
        aria-label={t("Import in progress")}
        onClick={() => openModal(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "imports" })}
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
        <ImportIndicator />
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
  const openImporter = useOpenImporter();
  const { selectedMailbox } = useMailboxContext();
  const canAccessDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);
  const canImportMessages = useAbility(Abilities.CAN_IMPORT_MESSAGES, selectedMailbox);
  const canManageMessageTemplates = useAbility(Abilities.CAN_MANAGE_MESSAGE_TEMPLATES, selectedMailbox);
  const isIntegrationsEnabled = useFeatureFlag(FEATURE_KEYS.MAILBOX_ADMIN_CHANNELS);
  const canManageIntegrations = canManageMessageTemplates && isIntegrationsEnabled;
  const canAdministrateSelectedMailbox = useAbility(Abilities.CAN_MANAGE_ACCESSES, selectedMailbox);
  const canOpenMailboxSettings = canAdministrateSelectedMailbox || canManageMessageTemplates || canManageIntegrations;
  // Notifications/devices are user-scoped, so every user sees this entry when
  // push is enabled — independent of any mailbox ability.
  const config = useConfig();
  const canManageNotifications = config.PUSH_ENABLED;
  const { t } = useTranslation();
  const navigate = useNavigate();

  const hasOptions = canAccessDomainAdmin || canImportMessages || canOpenMailboxSettings || canManageNotifications;
  // Live progress moved to the header ImportIndicator (which reads the imports
  // resource); the menu entry just opens the importer.
  const importMessageOption = {
    label: t("Import messages"),
    icon: <Upload />,
    callback: openImporter,
    showSeparator: canAccessDomainAdmin
  };

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
              ...(canManageNotifications ? [{
                label: t("Notifications"),
                icon: <Icon name="notifications" style={{ fontSize: 24 }} />,
                callback: () => openModal(MODAL_NOTIFICATIONS_ID),
                showSeparator: canAccessDomainAdmin,
              }] : []),
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
