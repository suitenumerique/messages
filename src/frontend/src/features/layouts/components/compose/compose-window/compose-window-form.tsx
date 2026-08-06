import { RefObject, useEffect } from "react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { MessageForm, MessageFormHandle } from "@/features/forms/components/message-form";
import { useComposeWindows } from "@/features/providers/compose-windows";
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
    const { closeWindow, updateWindow } = useComposeWindows();
    const { mailbox, draft, parentMessage, thread, isLoading, isDraftNotFound } = useComposeDraftData({
        mailboxId: descriptor.mailboxId,
        draftId: descriptor.draftId,
        parentMessageId: descriptor.parentMessageId,
        threadId: descriptor.threadId,
        initialDraft: descriptor.initialDraft,
        initialParent: descriptor.initialParent,
    });

    // The draft was deleted or made inaccessible elsewhere (other tab, other
    // client): drop the window silently.
    useEffect(() => {
        if (isDraftNotFound) {
            closeWindow(descriptor.windowId);
        }
    }, [isDraftNotFound, closeWindow, descriptor.windowId]);

    if (!mailbox || isLoading) {
        return (
            <div className="compose-window__loading">
                <Spinner />
            </div>
        );
    }

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
            onDraftChange={(nextDraft) => updateWindow(descriptor.windowId, { draftId: nextDraft?.id })}
            onSubjectChange={(subject) => updateWindow(descriptor.windowId, { title: subject })}
            onSuccess={() => closeWindow(descriptor.windowId)}
            onClose={() => closeWindow(descriptor.windowId)}
        />
    );
};
