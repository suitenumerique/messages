import {
  Modal,
  ModalSize,
  ModalTab,
} from "@gouvfr-lasuite/cunningham-react";
import { HorizontalSeparator } from "@gouvfr-lasuite/ui-kit";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { MailboxSelector } from "@/features/layouts/components/mailbox-selector";
import {
  useConfirmBeforeClose,
  useConfirmUnsavedChanges,
} from "@/features/hooks/use-confirm-before-close";
import { MailboxSettingsGeneralTab } from "./general-tab";
import { MailboxSettingsAccessTab } from "./access-tab";
import { MailboxSettingsSignaturesTab } from "./signatures-tab";
import { MailboxSettingsMessageTemplatesTab } from "./message-templates-tab";
import { MailboxSettingsAutorepliesTab } from "./autoreplies-tab";
import { MailboxSettingsIntegrationsTab } from "./integrations-tab";

export type SettingsTabId =
  | "general"
  | "access"
  | "signatures"
  | "message-templates"
  | "autoreplies"
  | "integrations";

type ModalMailboxSettingsProps = {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: SettingsTabId;
};

export const MODAL_MAILBOX_SETTINGS_ID = "modal-mailbox-settings";

// Below this width Cunningham's tab modal collapses into a sidebar→content
// drill-down (its internal `@media (max-width: 576px)`). We read the same
// breakpoint so the open-time tab selection matches the layout actually shown —
// the sidebar↔content view itself can't be driven from outside the component.
const COMPACT_MODAL_MEDIA_QUERY = "(max-width: 576px)";

/**
 * Settings modal for a mailbox the user can configure. Built on Cunningham's
 * "tab" modal layout: the sidebar lists setting categories (General — rename —,
 * access sharing, signatures, message templates, auto-replies and integrations) and
 * a macOS-style identity card in the sidebar header switches the mailbox being
 * configured. Each tab is shown only when the selected mailbox grants the
 * matching ability.
 *
 * Controlled via `isOpen`/`onClose` props. It is registered in the global modal
 * store (see `controlled-modals`) so a single instance lives high in the tree
 * and survives the header subtree remounting when the viewport crosses the
 * responsive breakpoint (burger menu toggling), which would otherwise tear the
 * open modal down with it. `initialTab` preselects a category at open (used by
 * the header auto-reply indicator to land directly on the auto-replies tab).
 */
export const ModalMailboxSettings = ({
  isOpen,
  onClose,
  initialTab,
}: ModalMailboxSettingsProps) => {
  const { t } = useTranslation();
  const { mailboxes, selectedMailbox } = useMailboxContext();
  const isIntegrationsEnabled = useFeatureFlag(FEATURE_KEYS.MAILBOX_ADMIN_CHANNELS);

  // Every mailbox the user can configure: admins (rename + accesses) plus any
  // mailbox where the user can manage message templates (signatures, templates,
  // auto-replies, integrations). Mailboxes granting neither have no settings to
  // show and are excluded entirely.
  const settingsMailboxes = useMemo(
    () =>
      (mailboxes ?? []).filter(
        (mailbox) =>
          mailbox.abilities.manage_accesses ||
          mailbox.abilities.manage_message_templates,
      ),
    [mailboxes],
  );

  const [selectedMailboxId, setSelectedMailboxId] = useState<string | null>(
    null,
  );

  const [isActiveTabDirty, setIsActiveTabDirty] = useState(false);
  const confirmUnsavedChanges = useConfirmUnsavedChanges();
  const guardedOnClose = useConfirmBeforeClose(isActiveTabDirty, onClose);

  const settingsMailbox =
    settingsMailboxes.find((mailbox) => mailbox.id === selectedMailboxId) ??
    (selectedMailbox?.abilities.manage_accesses ||
    selectedMailbox?.abilities.manage_message_templates
      ? selectedMailbox
      : null) ??
    settingsMailboxes[0] ??
    null;

  // The active tab is decided once, at open (the store mounts a fresh instance
  // each time the modal opens, so this initializer re-runs per open), and never
  // recomputed on resize. An explicit `initialTab` wins; otherwise, in the
  // compact layout we open with no tab selected ("" — Cunningham keeps an empty
  // string unselected, whereas undefined would fall back to the first tab) so
  // the modal shows the bare sidebar, and on the wider layout we preselect the
  // first tab the mailbox actually exposes (General when the user administers it,
  // otherwise Signatures) so the side-by-side content pane isn't empty. Cunningham
  // then drives the sidebar↔content view itself as the user picks tabs.
  const [activeTab, setActiveTab] = useState<SettingsTabId | "">(() => {
    if (initialTab) {
      return initialTab;
    }
    if (
      typeof window !== "undefined" &&
      window.matchMedia(COMPACT_MODAL_MEDIA_QUERY).matches
    ) {
      return "";
    }
    return settingsMailbox?.abilities.manage_accesses === false
      ? "signatures"
      : "general";
  });

  // Ordered ids of the tabs the selected mailbox exposes, gated by its abilities
  // (and the integrations feature flag). Single source of truth: it drives both
  // the rendered `tabs` below and the active-tab synchronisation on mailbox
  // switch, so the two can never drift apart.
  const availableTabIds = useMemo<SettingsTabId[]>(() => {
    if (!settingsMailbox) {
      return [];
    }
    const { manage_accesses, manage_message_templates } =
      settingsMailbox.abilities;
    const ids: SettingsTabId[] = [];
    if (manage_accesses) {
      ids.push("general", "access");
    }
    if (manage_message_templates) {
      ids.push("message-templates", "autoreplies", "signatures");
      if (isIntegrationsEnabled) {
        ids.push("integrations");
      }
    }
    return ids;
  }, [settingsMailbox, isIntegrationsEnabled]);

  // When the user drops their own admin rights from inside the modal (e.g. they
  // demote or remove their own admin access), the mailbox leaves the eligible
  // list. Fall back to another mailbox, or close the modal if none remain.
  useEffect(() => {
    if (isOpen && !settingsMailbox) {
      onClose();
    }
  }, [isOpen, settingsMailbox, onClose]);

  // Switching the configured mailbox keeps the current tab when that mailbox
  // still exposes it; only when the active tab disappears (the new mailbox lacks
  // the matching ability) do we fall back to its first available tab. An empty
  // selection (the compact bare-sidebar state) is left untouched.
  useEffect(() => {
    if (activeTab === "") {
      return;
    }
    if (!availableTabIds.includes(activeTab)) {
      setActiveTab(availableTabIds[0] ?? "");
    }
  }, [activeTab, availableTabIds]);

  if (!settingsMailbox) {
    return null;
  }

  // macOS-style account card switching the configured mailbox, shared with the
  // sidebar header switcher. Switching remounts the General tab and discards any
  // unsaved rename, so confirm first when there are pending edits.
  const sidebarHeader = (
    <>
      <MailboxSelector
        mailboxes={settingsMailboxes}
        selectedMailbox={settingsMailbox}
        onSelect={async (value) => {
          if (await confirmUnsavedChanges(isActiveTabDirty)) {
            setSelectedMailboxId(value);
          }
        }}
      />
      <HorizontalSeparator width="double" />
    </>
  );

  // Tabs are gated by `availableTabIds` (the single source of truth derived from
  // the mailbox abilities above), so this list and the synchronisation effect
  // stay in sync. `key={settingsMailbox.id}` remounts each tab's content when the
  // user switches the mailbox being configured.
  const tabs: ModalTab[] = [
    ...(availableTabIds.includes("general")
      ? [
          {
            id: "general",
            label: t("General"),
            title: t("General"),
            content: (
              <MailboxSettingsGeneralTab
                key={settingsMailbox.id}
                mailbox={settingsMailbox}
                onDirtyChange={setIsActiveTabDirty}
              />
            ),
          },
          {
            id: "access",
            label: t("Access sharing"),
            title: t("Access sharing to the mailbox"),
            content: (
              <MailboxSettingsAccessTab
                key={settingsMailbox.id}
                mailbox={settingsMailbox}
              />
            ),
          },
        ]
      : []),
    ...(availableTabIds.includes("message-templates")
      ? [
          {
            id: "message-templates",
            label: t("Message templates"),
            title: t("Message templates"),
            subtitle: <p className="mb-base mr-base">
            {t(
              "Create reusable message templates shared by all users of this mailbox.",
            )}</p>,
            content: (
              <MailboxSettingsMessageTemplatesTab
                key={settingsMailbox.id}
                mailbox={settingsMailbox}
              />
            ),
          },
          {
            id: "autoreplies",
            label: t("Auto-replies"),
            title: t("Auto-replies"),
            subtitle: <p className="mb-base mr-base">{
              t(
                "Set up automatic replies sent to senders while the mailbox is unattended."
                +" Only one auto-reply can be active at a time."
              )
            }</p>,
            content: (
              <MailboxSettingsAutorepliesTab
                key={settingsMailbox.id}
                mailbox={settingsMailbox}
              />
            ),
          },
          {
            id: "signatures",
            label: t("Signatures"),
            title: t("Signatures"),
            subtitle: <p className="mb-base mr-base">{
              t(
                "Create standardized signatures that can be used by all users of this mailbox.",
              )
            }</p>,
            content: (
              <MailboxSettingsSignaturesTab
                key={settingsMailbox.id}
                mailbox={settingsMailbox}
              />
            ),
          },
        ]
      : []),
    ...(availableTabIds.includes("integrations")
      ? [
          {
            id: "integrations",
            label: t("Integrations"),
            title: t("Integrations"),
            subtitle: <p className="mb-base mr-base">{
              t(
                "Connect external tools to this mailbox through widgets and API keys.",
              )
            }</p>,
            content: (
              <MailboxSettingsIntegrationsTab
                key={settingsMailbox.id}
                mailbox={settingsMailbox}
              />
            ),
          },
        ]
      : []),
  ];

  return (
    <Modal
      constraints={{
        preferredHeight: '80vh',
      }}
      isOpen={isOpen}
      aria-label={t("Settings")}
      onClose={guardedOnClose}
      size={ModalSize.LARGE}
      variant="tab"
      sidebarTitle={<div className="mailbox-settings__identity">{sidebarHeader}</div>}
      tabs={tabs}
      activeTab={activeTab}
      onTabChange={async (tabId) => {
        if (tabId === activeTab) {
          return;
        }
        // Switching tab unmounts the General tab and discards any unsaved
        // rename; confirm first when there are pending edits.
        if (await confirmUnsavedChanges(isActiveTabDirty)) {
          setActiveTab(tabId as SettingsTabId);
        }
      }}
    />
  );
};
