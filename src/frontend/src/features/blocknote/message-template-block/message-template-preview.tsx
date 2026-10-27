import { useMemo } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import DomPurify from "dompurify";
import { Spinner } from "@gouvfr-lasuite/ui-components";
import { useMailboxesMessageTemplatesRenderRetrieve } from "@/features/api/gen";
import { useHtmlWithObjectUrls } from "@/features/blocknote/image-block/use-html-with-object-urls";

type MessageTemplatePreviewProps = {
    mailboxId: string;
    templateId: string;
    signatureId?: string | null;
    messageId?: string;
};

/**
 * Read-only rendering of a message template followed by its signature, with
 * their placeholders resolved server-side the same way they will be once
 * inserted.
 */
export const MessageTemplatePreview = ({ mailboxId, templateId, signatureId, messageId }: MessageTemplatePreviewProps) => {
    const { t } = useTranslation();
    // The query key holds the draft id: a rendering stays valid for the whole
    // draft, so browsing back and forth through the list never refetches it.
    // Editing a template still invalidates it (see message-templates-cache).
    // keepPreviousData keeps the last preview on screen while the next one
    // loads instead of flashing a spinner.
    const renderParams = messageId ? { message_id: messageId } : undefined;
    const { data: { data: rendered = null } = {}, isLoading, isError } = useMailboxesMessageTemplatesRenderRetrieve(
        mailboxId,
        templateId,
        renderParams,
        { query: { staleTime: Infinity, placeholderData: keepPreviousData } },
    );
    // The template body never contains its signature: the composer exports
    // the signature block as an empty node and the signature is only
    // appended when the message is sent. It is rendered on its own here,
    // sharing its cache with the signature block of the composer. No
    // placeholder data: a template without signature must not show the
    // signature of the previously previewed one.
    const { data: { data: renderedSignature = null } = {}, isLoading: isLoadingSignature } = useMailboxesMessageTemplatesRenderRetrieve(
        mailboxId,
        signatureId ?? "",
        renderParams,
        { query: { enabled: !!signatureId, staleTime: Infinity } },
    );

    const htmlBody = rendered?.html_body;
    const signatureHtmlBody = signatureId ? renderedSignature?.html_body : undefined;
    const sanitizedHtml = useMemo(
        () => htmlBody ? DomPurify().sanitize(htmlBody + (signatureHtmlBody ?? "")) : null,
        [htmlBody, signatureHtmlBody],
    );
    const html = useHtmlWithObjectUrls(sanitizedHtml);

    if (isLoading || isLoadingSignature) {
        return (
            <div className="message-template-preview message-template-preview--status" role="status">
                <Spinner size="sm" />
                <span className="c__offscreen">{t("Loading preview...")}</span>
            </div>
        );
    }

    if (isError || !html) {
        return (
            <div className="message-template-preview message-template-preview--status">
                {t("No preview available")}
            </div>
        );
    }

    return (
        <div
            className="message-template-preview"
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
};
