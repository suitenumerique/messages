import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useMailboxContext } from "@/features/providers/mailbox";
import { IntegrationsPageContent } from "@/features/layouts/components/mailbox-settings/integrations-view/page-content";
import { CreateIntegrationAction } from "@/features/layouts/components/mailbox-settings/integrations-view/create-integration-action";
import { useFeatureFlag, FEATURE_KEYS } from "@/hooks/use-feature";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";

const MailboxIntegrationsPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { queryStates, selectedMailbox } = useMailboxContext();
  const isIntegrationsEnabled = useFeatureFlag(FEATURE_KEYS.MAILBOX_ADMIN_CHANNELS);

  useEffect(() => {
    if (!queryStates.mailboxes.isLoading && !selectedMailbox) {
      navigate({ to: "/" });
    }
  }, [queryStates.mailboxes.isLoading, selectedMailbox, navigate]);

  useEffect(() => {
    if (!isIntegrationsEnabled) {
      navigate({ to: "/" });
    }
  }, [isIntegrationsEnabled, navigate]);

  if (!isIntegrationsEnabled) return null;
  if (!selectedMailbox) return null;

  return (
    <div className="admin-page" id={SKIP_LINK_TARGET_ID}>
      <div className="admin-page__header">
        <h1 className="title">{t("Integrations")}</h1>
        <div className="admin-page__actions">
          <CreateIntegrationAction />
        </div>
      </div>

      <div className="admin-page__content">
        <IntegrationsPageContent />
      </div>
    </div>
  );
};

export const Route = createFileRoute("/mailbox/$mailboxId/integrations/")({
  component: MailboxIntegrationsPage,
});
