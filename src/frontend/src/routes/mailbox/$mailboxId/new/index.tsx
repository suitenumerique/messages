import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";

import { MessageForm } from "@/features/forms/components/message-form";
import { useMailboxContext } from "@/features/providers/mailbox";
import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";

const NewMessageFormPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const router = useRouter();
  const { queryStates, selectedMailbox } = useMailboxContext();

  const handleClose = () => {
    if (window.history.length > 1) {
      router.history.back();
    } else if (!selectedMailbox) {
      navigate({ to: '/' });
    } else {
      const defaultFolder = MAILBOX_FOLDERS()[0];
      navigate({ to: '/mailbox/$mailboxId', params: { mailboxId: selectedMailbox.id }, search: defaultFolder.filter });
    }
  };

  if (queryStates.mailboxes.isLoading) {
    return (
      <div className="thread-view thread-view--loading">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="new-message-form" id={SKIP_LINK_TARGET_ID}>
      <div className="new-message-form-container">
        <h1>{t("New message")}</h1>
        <MessageForm
          showSubject={true}
          onSuccess={handleClose}
          onClose={handleClose}
        />
      </div>
    </div>
  );
};

export const Route = createFileRoute("/mailbox/$mailboxId/new/")({
  component: NewMessageFormPage,
});
