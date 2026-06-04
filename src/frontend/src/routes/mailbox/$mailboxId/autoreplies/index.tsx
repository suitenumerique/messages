import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useMailboxContext } from "@/features/providers/mailbox";
import { ManageAutorepliesViewPageContent } from "@/features/layouts/components/mailbox-settings/autoreplies-view/page-content";
import { ComposeAutoreplyAction } from "@/features/layouts/components/mailbox-settings/autoreplies-view/compose-autoreply-action";
import { Banner } from "@/features/ui/components/banner";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";

const MailboxAutorepliesPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { queryStates, selectedMailbox } = useMailboxContext();

  useEffect(() => {
    if (!queryStates.mailboxes.isLoading && !selectedMailbox) {
      navigate({ to: "/" });
    }
  }, [queryStates.mailboxes.isLoading, selectedMailbox, navigate]);

  if (!selectedMailbox) return null;

  return (
    <div className="admin-page" id={SKIP_LINK_TARGET_ID}>
      <div className="admin-page__header">
        <h1 className="title">{t("Auto-replies for {{mailbox}}", { mailbox: selectedMailbox.email })}</h1>
        <div className="admin-page__actions">
          <ComposeAutoreplyAction />
        </div>
      </div>

      <div className="admin-page__content">
        <div className="mb-sm mt-base">
          <Banner type="info">
            {t('Auto-replies are configured per mailbox. Only one auto-reply can be active at a time.', { mailbox: selectedMailbox.email })}
          </Banner>
        </div>
        <ManageAutorepliesViewPageContent />
      </div>
    </div>
  );
};

export const Route = createFileRoute("/mailbox/$mailboxId/autoreplies/")({
  component: MailboxAutorepliesPage,
});
