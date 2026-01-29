"use client";
import * as locales from '@blocknote/core/locales';
import { useCreateBlockNote } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { BlockNoteEditor, BlockNoteSchema, defaultBlockSpecs, PartialBlock } from '@blocknote/core';
import { MessageTemplateSelector } from '@/features/blocknote/message-template-block';
import MailHelper from '@/features/utils/mail-helper';
import { FieldProps } from '@gouvfr-lasuite/cunningham-react';
import { useFormContext } from 'react-hook-form';
import { useEffect, useCallback } from 'react';
import { QuotedMessageBlock } from '@/features/blocknote/quoted-message-block';
import { Message } from '@/features/api/gen/models/message';
import { BlockNoteViewField } from '@/features/blocknote/blocknote-view-field';
import { Toolbar } from '@/features/blocknote/toolbar';
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from '@/features/blocknote/signature-block';
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList } from '@/features/api/gen';
import { MessageComposerHelper } from '@/features/utils/composer-helper';

const BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'signature': BlockSignature(),
        'quoted-message': QuotedMessageBlock(),
    }
});

export type MessageComposerBlockNoteSchema = typeof BLOCKNOTE_SCHEMA;
export type MessageComposerBlockSchema = MessageComposerBlockNoteSchema['blockSchema'];
export type MessageComposerInlineContentSchema = MessageComposerBlockNoteSchema['inlineContentSchema'];
export type MessageComposerStyleSchema = MessageComposerBlockNoteSchema['styleSchema'];
export type PartialMessageComposerBlockSchema = PartialBlock<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>;

export type QuoteType = "reply" | "forward";

type MessageComposerProps = FieldProps & {
    mailboxId: string;
    blockNoteOptions?: Partial<MessageComposerBlockNoteSchema>
    defaultValue?: string;
    disabled?: boolean;
    draft?: Message;
    submitDraft?: () => void;
    quotedMessage?: Message;
    quoteType?: QuoteType;
}

/**
 * A component that allows the user to edit a message in a BlockNote editor.
 * !!! This component must be used within a FormProvider (from react-hook-form)
 *
 * 4 hidden inputs (`htmlBody`, `textBody`, `draftBody`, `signatureId`) are rendered to store
 * the HTML, text, raw content of the message and the signature id used. Their values are updated
 * when the editor is blurred. About the signature, the value is updated immediately
 * when the signature block is changed. Those inputs must be used in the parent form
 * to retrieve all the content of the message.
 */

export const MessageComposer = ({ mailboxId, blockNoteOptions, defaultValue, quotedMessage, quoteType, disabled = false, draft, submitDraft, ...props }: MessageComposerProps) => {
    const form = useFormContext();
    const { t, i18n } = useTranslation();
    const { data: { data: activeSignatures = [] } = {}, isLoading: isLoadingSignatures } = useMailboxesMessageTemplatesAvailableList(
        mailboxId,
        {
            type: MessageTemplateTypeChoices.signature,
        },
        {
            query: {
                refetchOnMount: 'always',
                refetchOnWindowFocus: true,
            },
        }
    );


    /**
     * Prepare initial content of the editor
     * If the user is replying or forwarding a message, a quoted-message block is append
     * to display a preview of the quoted message.
     */
    const getInitialContent = () => {
        // Parse initial content
        const initialContent = defaultValue
            ? JSON.parse(defaultValue)
            : [{ type: "paragraph", content: "" }];

        if (!quotedMessage) return initialContent;
        return initialContent.concat([{
            type: "quoted-message",
            content: undefined,
            props: {
                mode: quoteType!,
                messageId: quotedMessage.id,
                subject: quotedMessage.subject,
                recipients: quotedMessage.to.map((to) => to.contact.email).join(", "),
                sender: quotedMessage.sender.email,
                received_at: quotedMessage.created_at
            }
        }]);
    };

    const locale = i18n.resolvedLanguage?.split('-')[0] || 'en';
    const editor = useCreateBlockNote({
        schema: BLOCKNOTE_SCHEMA,
        tabBehavior: "prefer-navigate-ui",
        trailingBlock: false,
        initialContent: getInitialContent(),
        dictionary: {
            ...(locales[locale as keyof typeof locales] || locales.en),
            placeholders: {
                ...(locales[locale as keyof typeof locales] || locales.en).placeholders,
                emptyDocument: t('Start typing...'),
                default: t('Start typing...'),
            }
        },
        ...blockNoteOptions,
    }, [locale]);

    const handleChange = useCallback(async (editor: BlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>, submitNeeded: boolean = true) => {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        form.setValue("messageDraftBody", JSON.stringify(editor.document), { shouldDirty: true });
        form.setValue("messageTextBody", markdown);
        form.setValue("messageHtmlBody", html);

        // Update signatureId
        const signatureBlock = editor.getBlock('signature');
        const signatureId = (signatureBlock?.type === 'signature' ? signatureBlock.props.templateId : undefined);
        form.setValue("signatureId", signatureId);

        // If signature block has changed, fire update immediately
        if (submitNeeded && signatureId !== draft?.signature?.id) {
            submitDraft?.();
        }
    }, [form, submitDraft, draft?.signature?.id]);

    /**
     * Process the html and text content of the message when the editor is mounted.
     */
    useEffect(() => {
        if (!editor) return;
        handleChange(editor,false);
    }, [editor])

    useEffect(() => {
        if (!editor || isLoadingSignatures) return;

        // Determine which signature should be used based on priority
        let signatureToUse = undefined;

        // Priority 1: Forced signature (always applies, regardless of draft state)
        const forcedSignature = activeSignatures.find(signature => signature.is_forced);
        if (forcedSignature) {
            signatureToUse = forcedSignature;
        }
        // Priority 2: Draft signature (if exists and is still active) or nothing if draft has no signature
        else if (draft) {
            // If draft has a signature that's still active, use it
            if (draft.signature?.id && activeSignatures.some(signature => signature.id === draft.signature!.id)) {
                signatureToUse = draft.signature;
            }
            // If draft exists but has no signature, don't apply any (user may have removed it intentionally)
            // signatureToUse remains undefined
        }
        // Priority 3: Default signature (only for new messages, not drafts)
        else {
            signatureToUse = activeSignatures.find(signature => signature.is_default);
        }

        // Check if signature is already in the editor
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            const blockSignatureId = (signatureBlock.props as BlockSignatureConfigProps).templateId;
            const isSignatureStale = activeSignatures.findIndex(signature => signature.id === blockSignatureId) < 0;
            // Forced signature must always be applied, even if different from current
            const forcedSignatureMismatch = forcedSignature && forcedSignature.id !== blockSignatureId;
            // For drafts with a specific signature, check if it matches
            const draftSignatureMismatch = draft?.signature?.id && draft.signature.id !== blockSignatureId && !forcedSignature;
            // For new messages, update if the default signature changed
            const shouldUpdateToNewDefault = !draft && signatureToUse && signatureToUse.id !== blockSignatureId;

            if (isSignatureStale || forcedSignatureMismatch || draftSignatureMismatch || shouldUpdateToNewDefault) {
                editor.removeBlocks(["signature"]);
            } else {
                return;
            }
        }

        if (activeSignatures.length === 0) return;

        // Add signature block if we have a signature to use
        if (signatureToUse) {
            // Add signature at the end of the document
            const signatureBlock = {
                id: "signature",
                type: "signature" as const,
                props: {
                    templateId: signatureToUse.id,
                    mailboxId: mailboxId,
                }
            };

            // Insert at the end
            if (editor.document.length === 0) {
                editor.insertBlocks([{ type: "paragraph", content: [{ type: "text", text: "", styles: {} }] }], "", "after");
            }

            // Put signature at the end of the document or before the quote block if it exists
            MessageComposerHelper.insertSignatureBlock(editor, signatureBlock);

            // Set the signatureId in the form
            form.setValue('signatureId', signatureToUse.id);
        } else {
            // Set signatureId to undefined after a microtask to avoid flushSync issues
            form.setValue('signatureId', undefined);
        }
    }, [editor, isLoadingSignatures, activeSignatures, draft?.signature?.id]);

    return (
        <>
            <BlockNoteViewField
                {...props}
                disabled={disabled}
                composerProps={{
                    editor,
                    onChange: (editor) => handleChange(editor, true),
                }}
            >
                <Toolbar>
                    <MessageTemplateSelector
                        mailboxId={mailboxId}
                        context={{
                            recipient_name: draft
                                ? draft.to.map(to => to.contact.name).join(", ")
                                : quotedMessage?.sender?.name || ""
                        }}
                    />
                    <SignatureTemplateSelector
                        templates={activeSignatures}
                        isLoading={isLoadingSignatures}
                        mailboxId={mailboxId}
                        defaultSelected={draft?.signature?.id}
                    />
                </Toolbar>
            </BlockNoteViewField>
            <input {...form.register("messageHtmlBody")} type="hidden" />
            <input {...form.register("messageTextBody")} type="hidden" />
            <input {...form.register("messageDraftBody")} type="hidden" />
            <input {...form.register("signatureId")} type="hidden" />
        </>
    );
};

