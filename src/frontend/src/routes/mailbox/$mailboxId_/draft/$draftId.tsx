import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";

import AuthenticatedView from "@/features/layouts/components/main/authenticated-view";
import { MailboxProvider } from "@/features/providers/mailbox";
import { SentBoxProvider } from "@/features/providers/sent-box";
import { AttachmentPreviewProvider } from "@/features/providers/attachment-preview";
import { AttachmentPreviewModal } from "@/features/layouts/components/thread-view/components/attachment-preview-modal";
import { MessageForm } from "@/features/forms/components/message-form";
import { useComposeDraftData } from "@/features/layouts/components/compose/use-compose-draft-data";
import { postComposeBroadcast } from "@/features/providers/compose-windows/broadcast";
import { Toaster } from "@/features/ui/components/toaster";
import { useTheme } from "@/features/providers/theme";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { useDocumentTitle } from "@/hooks/use-document-title";

const ComposeStandaloneContent = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { mailboxId, draftId } = Route.useParams();
  const { mailbox, draft, parentMessage, thread, isLoading, isDraftNotFound } = useComposeDraftData({
    mailboxId,
    draftId,
  });
  // The tab title follows the subject as it is typed, like the window title
  // of the docked counterpart.
  const [editedSubject, setEditedSubject] = useState<string>();
  useDocumentTitle((editedSubject ?? draft?.subject)?.trim() || t("New message"));

  // Opened via window.open from the main tab: closing the tab is the natural
  // exit. When the tab was opened directly (deep link), fall back to the app.
  const handleExit = () => {
    window.close();
    navigate({ to: "/mailbox/$mailboxId", params: { mailboxId }, replace: true });
  };

  if (isDraftNotFound || (!isLoading && !mailbox)) {
    return (
      <div className="compose-standalone__empty">
        <p>{t("This draft is no longer available.")}</p>
      </div>
    );
  }

  if (isLoading || !draft) {
    return (
      <div className="compose-standalone__loading">
        <Spinner />
      </div>
    );
  }

  const broadcast = (type: "draft-updated" | "draft-sent" | "draft-deleted") =>
    postComposeBroadcast({ type, draftId, threadId: draft.thread_id ?? undefined, mailboxId });

  return (
    <MessageForm
      standalone
      mode={draft.parent_id ? "reply" : "new"}
      mailboxOverride={mailbox}
      threadOverride={thread}
      draftMessage={draft}
      parentMessage={parentMessage}
      onDraftChange={(nextDraft) => broadcast(nextDraft ? "draft-updated" : "draft-deleted")}
      onSubjectChange={setEditedSubject}
      onSuccess={() => {
        broadcast("draft-sent");
        handleExit();
      }}
      onClose={handleExit}
    />
  );
};

const ComposeStandalonePage = () => {
  const { t } = useTranslation();
  const { theme, variant } = useTheme();

  return (
    <AuthenticatedView>
      <MailboxProvider>
        <SentBoxProvider>
          <AttachmentPreviewProvider>
            <div className="compose-standalone" id={SKIP_LINK_TARGET_ID}>
              <header className="compose-standalone__header">
                <img src={`/images/${theme}/app-logo-${variant}.svg`} alt={t("logo")} height={32} />
              </header>
              <main className="compose-standalone__main">
                <ComposeStandaloneContent />
              </main>
            </div>
            <AttachmentPreviewModal />
            {/* The app shell normally hosts the toaster; this page has no shell. */}
            <Toaster />
          </AttachmentPreviewProvider>
        </SentBoxProvider>
      </MailboxProvider>
    </AuthenticatedView>
  );
};

export const Route = createFileRoute("/mailbox/$mailboxId_/draft/$draftId")({
  component: ComposeStandalonePage,
});
