"use client";
import * as locales from '@blocknote/core/locales';
import { useCreateBlockNote } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { BlockNoteSchema, defaultBlockSpecs, PartialBlock } from '@blocknote/core';
import MailHelper from '@/features/utils/mail-helper';
import { FieldProps } from '@openfun/cunningham-react';
import { useFormContext } from 'react-hook-form';
import { useEffect, useCallback } from 'react';
import { QuotedMessageBlock } from '@/features/blocknote/quoted-message-block';
import { Message } from '@/features/api/gen/models/message';
import { BlockNoteViewField } from '@/features/blocknote/blocknote-view-field';
import { Toolbar } from '@/features/blocknote/toolbar';
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from '@/features/blocknote/signature-block';
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList } from '@/features/api/gen';

const BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'signature': BlockSignature,
        'quoted-message': QuotedMessageBlock,
    }
});

export type MessageComposerBlockNoteSchema = typeof BLOCKNOTE_SCHEMA;
export type MessageComposerBlockSchema = MessageComposerBlockNoteSchema['blockSchema'];
export type MessageComposerInlineContentSchema = MessageComposerBlockNoteSchema['inlineContentSchema'];
export type MessageComposerStyleSchema = MessageComposerBlockNoteSchema['styleSchema'];
export type PartialMessageComposerBlockSchema = PartialBlock<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>;

type MessageComposerProps = FieldProps & {
    mailboxId?: string;
    blockNoteOptions?: Partial<MessageComposerBlockNoteSchema>
    defaultValue?: string;
    quotedMessage?: Message;
    disabled?: boolean;
    draft?: Message;
    onSaveDraft?: () => void;
}

/**
 * A component that allows the user to edit a message in a BlockNote editor.
 * !!! This component must be used within a FormProvider (from react-hook-form)
 *
 * 3 hidden inputs (`htmlBody`, `textBody` and `draftBody`) are rendered to store
 * the HTML, text and raw content of the message. Their values are updated
 * when the editor is blurred. Those inputs must be used in the parent form
 * to retrieve all the content of the message.
 */
export const MessageComposer = ({ mailboxId, blockNoteOptions, defaultValue, quotedMessage, disabled = false, draft, onSaveDraft, ...props }: MessageComposerProps) => {
    const form = useFormContext();
    const { t, i18n } = useTranslation();
    const { data: { data: activeSignatures = [] } = {}, isLoading: isLoadingSignatures } = useMailboxesMessageTemplatesAvailableList(
        mailboxId || "",
        {
            query: { 
                enabled: !!mailboxId,
            },
            request: {
                params: {
                    type: MessageTemplateTypeChoices.signature.toUpperCase(),
                }
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
                mode: "forward",
                messageId: quotedMessage.id,
                subject: quotedMessage.subject,
                recipients: quotedMessage.to.map((to) => to.email).join(", "),
                sender: quotedMessage.sender.email,
                received_at: quotedMessage.created_at
            }
        }]);
    };

    const editor = useCreateBlockNote({
        schema: BLOCKNOTE_SCHEMA,
        tabBehavior: "prefer-navigate-ui",
        trailingBlock: false,
        initialContent: getInitialContent(),
        dictionary: {
            ...(locales[(i18n.resolvedLanguage) as keyof typeof locales] || locales.en),
            placeholders: {
                ...(locales[(i18n.resolvedLanguage) as keyof typeof locales] || locales.en).placeholders,
                emptyDocument: t('message_composer.start_typing'),
                default: t('message_composer.start_typing'),
            }
        },
        ...blockNoteOptions,
    }, [i18n.resolvedLanguage]);

    const handleChange = useCallback(async () => {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        form.setValue("messageDraftBody", JSON.stringify(editor.document), { shouldDirty: true });
        form.setValue("messageTextBody", markdown);
        form.setValue("messageHtmlBody", html);
        // Update signatureId
        const signatureBlock = editor.getBlock('signature');
        const signatureId = (signatureBlock?.type === 'signature' ? signatureBlock.props.templateId : null) || null;
        form.setValue("signatureId", signatureId, { shouldDirty: true });

    }, [editor, form]);
                    
            

    
    /**
     * Process the html and text content of the message when the editor is mounted.
     */
    useEffect(() => {
        if (!editor) return;

        // Update the form with the current signature ID
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock?.type === 'signature') {
            const signatureId = signatureBlock.props.templateId;
            form.setValue('signatureId', signatureId, { shouldDirty: true });
        }

        // Wait for editor to be fully ready before calling handleChange
        const checkEditorReady = () => {
            // Check if editor is ready, not headless, and has the required methods
            if (editor.document && 
                editor.isEditable !== false && 
                typeof editor.blocksToMarkdownLossy === 'function' &&
                editor._tiptapEditor && 
                !editor._tiptapEditor.isDestroyed) {
                handleChange();
            } else {
                // Retry after a short delay
                setTimeout(checkEditorReady, 100);
            }
        };

        // Start checking after initial delay
        setTimeout(checkEditorReady, 200);
    }, [editor])

    /**
     * Add signature to editor after signatures are loaded
     */
    useEffect(() => {
        if (!editor || isLoadingSignatures || activeSignatures.length === 0) return;

        // Check if signature is already in the editor
        const existingSignature = editor.getBlock('signature');
        if (existingSignature) {
            // In case there is a signature block but the templateId does not match an active signature, we remove the block.
            const isSignatureActive = activeSignatures.findIndex(signature => signature.id === (existingSignature.props as BlockSignatureConfigProps).templateId);
            if (isSignatureActive === -1) editor.removeBlocks(["signature"]);
            else return;
        }

        let signatureToUse = undefined;
        
        // Priority 1: Draft signature (if exists and is still active)
        if (draft?.signature?.id && activeSignatures.some(sig => sig.id === draft.signature?.id)) {
            signatureToUse = draft.signature;
        } 
        // Priority 2: Forced signature (if no draft signature)
        else {
            signatureToUse = activeSignatures.find(signature => signature.is_forced);
        }

        // Add signature block if we have a signature to use
        if (signatureToUse) {
            // Add signature at the end of the document
            const signatureBlock = {
                id: "signature",
                type: "signature" as const,
                props: {
                    templateId: signatureToUse.id,
                    mailboxId: mailboxId,
                    username: "",
                }
            };

            // Insert blocks after a microtask to avoid flushSync issues
            setTimeout(() => {
                // Insert at the end
                if (editor.document.length === 0) {
                    editor.insertBlocks([{ type: "paragraph", content: [{ type: "text", text: "", styles: {} }] }], "", "after");
                }
                
                editor.insertBlocks(
                    [signatureBlock],
                    editor.document[editor.document.length - 1].id,
                    "after"
                );
                
                // Set the signatureId in the form
                form.setValue('signatureId', signatureToUse.id, { shouldDirty: true });
            }, 0);
        } else {
            // Set signatureId to undefined after a microtask to avoid flushSync issues
            setTimeout(() => {
                form.setValue('signatureId', undefined, { shouldDirty: true });
            }, 0);
        }
    }, [editor, isLoadingSignatures, activeSignatures, draft?.signature?.id, mailboxId]);

    return (
        <>
            <BlockNoteViewField
                {...props}
                disabled={disabled}
                composerProps={{
                    editor,
                    onChange: handleChange,
                }}
            >
                <Toolbar>
                    <SignatureTemplateSelector 
                        templates={activeSignatures} 
                        isLoading={isLoadingSignatures} 
                        mailboxId={mailboxId}
                        onSignatureChange={(signatureId) => {
                            // Update form value and trigger draft save
                            form.setValue('signatureId', signatureId, { shouldDirty: true });
                            if (onSaveDraft) {
                                onSaveDraft();
                            }
                        }}
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

