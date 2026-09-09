import { RefObject, useEffect } from "react";
import { Button, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { MessageForm, MessageFormHandle } from "@/features/forms/components/message-form";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { postComposeBroadcast } from "@/features/providers/compose-windows/broadcast";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { useComposeDraftData } from "../use-compose-draft-data";

type ComposeWindowFormProps = {
    descriptor: ComposeWindowDescriptor;
    formRef: RefObject<MessageFormHandle | null>;
};

/**
 * Resolves the data a floating compose window needs (draft, parent message,
 * thread, mailbox) then renders the compact message form pinned to that
 * context.
 */
export const ComposeWindowForm = ({ descriptor, formRef }: ComposeWindowFormProps) => {
    const { t } = useTranslation();
    const { closeWindow, updateWindow } = useComposeWindows();
    const { mailbox, draft, parentMessage, thread, isLoading, isDraftNotFound, loadError, retry } = useComposeDraftData({
        mailboxId: descriptor.mailboxId,
        draftId: descriptor.draftId,
        parentMessageId: descriptor.parentMessageId,
        threadId: descriptor.threadId,
    });

    // The draft was sent, deleted or made inaccessible elsewhere (other tab,
    // other client): drop the window silently.
    useEffect(() => {
        if (isDraftNotFound) {
            closeWindow(descriptor.windowId);
        }
    }, [isDraftNotFound, closeWindow, descriptor.windowId]);

    // A restored window whose draft cannot be fetched right now (offline,
    // 5xx, 403) keeps its slot: the draft is most likely still there, and
    // the header Close remains the way out.
    if (loadError) {
        return (
            <div className="compose-window__error" role="alert">
                <p>{t("This draft could not be loaded.")}</p>
                <Button type="button" size="small" variant="secondary" onClick={retry}>
                    {t("Retry")}
                </Button>
            </div>
        );
    }

    if (!mailbox || isLoading || isDraftNotFound) {
        return (
            <div className="compose-window__loading">
                <Spinner />
            </div>
        );
    }

    // The same draft may be open in another tab (windows are restored from a
    // shared storage): tell it the draft is gone so it drops its own window
    // instead of persisting it back at its next change.
    const broadcastDraftGone = (type: "draft-sent" | "draft-deleted", draftId: string) =>
        postComposeBroadcast({
            type,
            draftId,
            threadId: draft?.thread_id ?? descriptor.threadId,
            mailboxId: descriptor.mailboxId,
        });

    return (
        <MessageForm
            ref={formRef}
            variant="compact"
            standalone
            mode={descriptor.mode}
            mailboxOverride={mailbox}
            threadOverride={thread}
            draftMessage={draft}
            parentMessage={parentMessage}
            onDraftChange={(nextDraft) => {
                if (!nextDraft && descriptor.draftId) broadcastDraftGone("draft-deleted", descriptor.draftId);
                updateWindow(descriptor.windowId, { draftId: nextDraft?.id });
            }}
            onSubjectChange={(subject) => updateWindow(descriptor.windowId, { title: subject })}
            onSuccess={() => {
                if (descriptor.draftId) broadcastDraftGone("draft-sent", descriptor.draftId);
                closeWindow(descriptor.windowId);
            }}
            onClose={() => closeWindow(descriptor.windowId)}
        />
    );
};
