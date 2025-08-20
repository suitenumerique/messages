"use client";
import * as locales from '@blocknote/core/locales';
import { useCreateBlockNote } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { BlockNoteSchema, defaultBlockSpecs, PartialBlock } from '@blocknote/core';
import MailHelper from '@/features/utils/mail-helper';
import { FieldProps } from '@openfun/cunningham-react';
import { useFormContext } from 'react-hook-form';
import { useEffect } from 'react';
import { QuotedMessageBlock } from '@/features/blocknote/quoted-message-block';
import { Message } from '@/features/api/gen/models/message';
import { BlockNoteViewField } from '@/features/blocknote/blocknote-view-field';
import { Toolbar } from '@/features/blocknote/toolbar';
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from '@/features/blocknote/signature-block';
import { MessageTemplateKindChoices, useMessageTemplatesList } from '@/features/api/gen';

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
export const MessageComposer = ({ mailboxId, blockNoteOptions, defaultValue, quotedMessage, disabled = false, ...props }: MessageComposerProps) => {
    const form = useFormContext();
    const { t, i18n } = useTranslation();
    const { data: { data: activeSignatures = [] } = {}, isLoading: isLoadingSignatures } = useMessageTemplatesList({
        query: { enabled: !!mailboxId },
        request: {
            params: {
                mailbox_id: mailboxId!,
                kind: MessageTemplateKindChoices.signature,
                is_active: "true",
            }
        },
    });

    /**
     * Prepare initial content of the editor
     * If the user is replying or forwarding a message, a quoted-message block is append
     * to display a preview of the quoted message.
     */
    const getInitialContent = () => {
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

    const handleChange = async () => {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        form.setValue("messageDraftBody", JSON.stringify(editor.document), { shouldDirty: true });
        form.setValue("messageTextBody", markdown);
        form.setValue("messageHtmlBody", html);
    }

    /**
     * Process the html and text content of the message when the editor is mounted.
     */
    useEffect(() => {
        handleChange();
    }, [])

    /**
     * Effect to insert/replace the default signature.
     * Futhermore to prevent issues, if a draft contains a stale signature, we remove it.
     */
    useEffect(() => {
        const signatureBlock = editor.getBlock('signature');
        const defaultSignatureTemplateId = activeSignatures.find(signature => signature.is_default)?.id
        if (defaultSignatureTemplateId && !(signatureBlock?.props as BlockSignatureConfigProps | undefined)?.templateId) {
            if (!signatureBlock) {
                editor.insertBlocks(
                    [{ id: "signature", type: "signature", props: { templateId: defaultSignatureTemplateId, mailboxId: mailboxId } }],
                    editor.document[0].id,
                    "after"
                );
            }
            else {
                editor.replaceBlocks(
                    ["signature"],
                    [{ id: "signature", type: "signature", props: { templateId: defaultSignatureTemplateId, mailboxId: mailboxId } }],
                );
            }
        }
        // In case there is a signature block but the templateId does not match an active signature, we remove the block.
        if (signatureBlock) {
            const isSignatureActive = activeSignatures.findIndex(signature => signature.id === (signatureBlock.props as BlockSignatureConfigProps).templateId);
            if (isSignatureActive === -1) editor.removeBlocks(["signature"]);
        }
    }, [isLoadingSignatures]);

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
                    <SignatureTemplateSelector templates={activeSignatures} isLoading={isLoadingSignatures} mailboxId={mailboxId} />
                </Toolbar>
            </BlockNoteViewField>
            <input {...form.register("messageHtmlBody")} type="hidden" />
            <input {...form.register("messageTextBody")} type="hidden" />
            <input {...form.register("messageDraftBody")} type="hidden" />
        </>
    );
};
