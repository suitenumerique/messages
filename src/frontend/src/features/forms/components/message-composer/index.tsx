"use client";
import { useCreateBlockNote } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { BlockNoteEditor, BlockNoteEditorOptions, BlockNoteSchema, defaultBlockSpecs, PartialBlock } from '@blocknote/core';
import { MessageTemplateSelector } from '@/features/blocknote/message-template-block';
import { imageBlockSpec, ALLOWED_IMAGE_MIME_TYPES } from '@/features/blocknote/image-block';
import { EmailExporter } from '@/features/blocknote/email-exporter';
import { blocksToMarkdown } from '@/features/blocknote/markdown-exporter';

import { FieldProps } from '@gouvfr-lasuite/cunningham-react';
import { useFormContext } from 'react-hook-form';
import React, { useEffect, useImperativeHandle, useRef } from 'react';
import { QuotedMessageBlock } from '@/features/blocknote/quoted-message-block';
import { Message } from '@/features/api/gen/models/message';
import { BlockNoteViewField } from '@/features/blocknote/blocknote-view-field';
import { Toolbar } from '@/features/blocknote/toolbar';
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from '@/features/blocknote/signature-block';
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList } from '@/features/api/gen';
import { Attachment } from '@/features/api/gen/models/attachment';
import { MessageComposerHelper } from '@/features/utils/composer-helper';
import { SmartTrailingBlock } from '@/features/blocknote/smart-trailing-block';
import { createBlockNoteDictionary } from '@/features/blocknote/utils';
import { MessageFormValues } from '../message-form';
import { DriveFile } from '../message-form/drive-attachment-picker';


// Re-export for consumers that import from message-composer
export { ALLOWED_IMAGE_MIME_TYPES } from '@/features/blocknote/image-block';

export const BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'image': imageBlockSpec,
        'signature': BlockSignature(),
        'quoted-message': QuotedMessageBlock(),
    }
});

export type MessageComposerBlockNoteSchema = typeof BLOCKNOTE_SCHEMA;
export type MessageComposerBlockSchema = MessageComposerBlockNoteSchema['blockSchema'];
export type MessageComposerInlineContentSchema = MessageComposerBlockNoteSchema['inlineContentSchema'];
export type MessageComposerStyleSchema = MessageComposerBlockNoteSchema['styleSchema'];
export type PartialMessageComposerBlockSchema = PartialBlock<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>;

const emailExporter = new EmailExporter();

export type QuoteType = "reply" | "forward";

export type MessageComposerHandle = {
    exportContent: () => Promise<{ htmlBody: string; textBody: string }>;
};

type MessageComposerProps = FieldProps & {
    mailboxId: string;
    blockNoteOptions?: Partial<BlockNoteEditorOptions<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>>,
    defaultValue?: string;
    disabled?: boolean;
    draft?: Message;
    submitDraft?: () => void;
    ensureDraft?: () => Promise<string | undefined>;
    quotedMessage?: Message;
    quoteType?: QuoteType;
    uploadInlineImage: (file: File) => Promise<{ url: string; blobId: string } | null>;
    uploadFiles: (files: File[]) => Promise<void>;
    removeInlineImage: (blobId: string) => void;
    attachments: (Attachment | DriveFile)[];
}

/**
 * A component that allows the user to edit a message in a BlockNote editor.
 * !!! This component must be used within a FormProvider (from react-hook-form)
 *
 * 2 hidden inputs (`draftBody`, `signatureId`) are rendered to store the raw
 * content of the message and the signature id used. HTML and text body are
 * generated on demand via `exportContent()` (exposed through ref) to avoid
 * creating real DOM elements on every keystroke.
 */

export const MessageComposer = React.forwardRef<MessageComposerHandle, MessageComposerProps>(({ mailboxId, blockNoteOptions, defaultValue, quotedMessage, quoteType, disabled = false, draft, submitDraft, ensureDraft, uploadInlineImage, uploadFiles, removeInlineImage, attachments, ...props }, ref) => {
    const form = useFormContext<MessageFormValues>();
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

    // Guard to prevent image load listeners from calling handleChange after unmount.
    const isMountedRef = useRef(true);

    // Track image blocks whose <img> is still loading, to avoid duplicate listeners.
    const loadingImageBlocksRef = useRef(new Set<string>());

    // Previous set of image block URLs in the editor, used to detect
    // which inline images the user removed between two onChange calls.
    const prevImageUrlsRef = useRef(new Set<string>());

    // Keep stable refs so callbacks captured by the BlockNote editor
    // (which only re-creates when [locale] changes) always read the
    // latest values without stale closures.
    const attachmentsRef = useRef(attachments);
    attachmentsRef.current = attachments;

    const uploadInlineImageRef = useRef(uploadInlineImage);
    uploadInlineImageRef.current = uploadInlineImage;

    const uploadFilesRef = useRef(uploadFiles);
    uploadFilesRef.current = uploadFiles;

    const editorRef = useRef<BlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>>(null);

    const uploadFile = async (file: File, blockId?: string) => {
        const attachment = await uploadInlineImageRef.current(file);
        if (!attachment) {
            if (blockId) {
                setTimeout(() => editorRef.current?.removeBlocks([blockId]), 0);
            }
            return '';
        }
        return attachment.url;
    };

    // Intercept non-image file drops/pastes before BlockNote processes them.
    // Without this, BlockNote routes unknown MIME types to the "file" block
    // (removed from schema) which causes a crash.
    //
    // BlockNote's SideMenu plugin dispatches synthetic drop events (isTrusted=false)
    // on the editor when the real drop lands within 250px of the editor bounds.
    // Furthermore when a drop is outside the editor boundary, it is not trusted and
    // event client coordinates are the same as the editor boundaries.
    // This causes duplicate uploads when the user drops on the attachment uploader
    // located below the editor. We consume these synthetic file drops early to
    // prevent any processing while still allowing synthetic block-drag events
    // (which carry no files) to pass through.
    // https://github.com/TypeCellOS/BlockNote/blob/da821317f19adc5596a8f0f1128b948b001a87a3/packages/core/src/extensions/SideMenu/SideMenu.ts#L365-L377
    const interceptNotAllowedFiles = (files: File[]) => {
        const nonImageFiles = files.filter(f => !ALLOWED_IMAGE_MIME_TYPES.includes(f.type));
        if (nonImageFiles.length === 0) return false;
        uploadFilesRef.current(nonImageFiles.filter(f => f.type));
        return true;
    };

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
        uploadFile,
        dictionary: createBlockNoteDictionary(locale, t),
        ...blockNoteOptions,
        _tiptapOptions: {
            extensions: [SmartTrailingBlock],
            editorProps: {
                handleDOMEvents: {
                    blur: (_view: unknown, event: FocusEvent) => {
                        // If focus moves to another element within the BlockNote
                        // container (bn-container), this is not a real blur.
                        // This happens when clicking on image toolbar buttons,
                        // formatting toolbar, etc. which are siblings of bn-editor.
                        const container = (event.target as HTMLElement).closest('.bn-container');
                        if (container?.contains(event.relatedTarget as Node)) {
                            return false;
                        }
                        const cursorPos = editor.getTextCursorPosition();
                        if (cursorPos.block.content === undefined) {
                            const target = [cursorPos.nextBlock, cursorPos.prevBlock]
                                .find((b) => b?.content !== undefined);
                            if (!target) {
                                editor.insertBlocks(
                                    [{ type: 'paragraph' }],
                                    cursorPos.block.id,
                                    'after',
                                );
                            }
                            const blockIdx = editor.document.findIndex(
                                (b) => b.id === cursorPos.block.id,
                            );
                            const dest = target || editor.document[blockIdx + 1];
                            if (dest) {
                                editor.setTextCursorPosition(dest.id);
                            }
                        }
                        return false;
                    },
                    drop: (_view: unknown, event: DragEvent) => {
                        const files = Array.from(event.dataTransfer?.files || []);
                        if (files.length === 0) return;

                        // Only check if the drop is inside the editor boundary.
                        let prevent = false;
                        if (!event.isTrusted) {
                            const editorEl = (event.target as HTMLElement).firstElementChild;
                            if (!editorEl) return false;
                            const boundaries = editorEl.getBoundingClientRect();
                            const { clientX, clientY } = event;
                            const isOutsideEditorBoundary = clientX === boundaries.left || clientX === boundaries.right || clientY === boundaries.top || clientY === boundaries.bottom;
                            prevent = isOutsideEditorBoundary;
                        }
                        if (!prevent) {
                            prevent = interceptNotAllowedFiles(files);
                        }

                        if (prevent) {
                            event.preventDefault();
                            return true;
                        }
                        return false;
                    },
                    paste: (_view: unknown, event: ClipboardEvent) => {
                        const files = Array.from(event.clipboardData?.files || []);
                        if (files.length === 0) return;
                        return interceptNotAllowedFiles(files);
                    }
                },
            },
        },
    }, [locale]);

    // Expose an export function so the parent can generate HTML and text body
    // on demand (e.g. at send time) instead of on every keystroke.
    // This avoids calling blocksToMarkdownLossy in handleChange, which creates
    // real <img> DOM elements via ProseMirror's DOMSerializer and triggers
    // unwanted blob download requests for inline images.
    useImperativeHandle(ref, () => ({
        exportContent: async () => {
            const blocks = editor.document;
            const textBody = await blocksToMarkdown(editor, blocks);
            const htmlBody = emailExporter.exportBlocks(blocks, editor.domElement ?? null);
            return { htmlBody, textBody };
        },
    }), [editor]);

    /**
     * Register one-time load listeners on image blocks whose <img> is still
     * loading. Once ALL pending images have loaded, handleChange is re-triggered
     * so the exported HTML includes the correct width attributes.
     * Width resolution itself is handled by the imageBlockSpec's toExternalHTML.
     */
    const registerImageLoadListeners = (editor: BlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>) => {
        editor.document.forEach(block => {
            if (block.type !== 'image' || !block.props.url) return;
            if (loadingImageBlocksRef.current.has(block.id)) return;

            const imgEl = editor.domElement?.querySelector<HTMLImageElement>(
                `[data-id="${block.id}"] img`,
            );
            if (!imgEl || imgEl.complete) return;

            loadingImageBlocksRef.current.add(block.id);
            const onSettled = () => {
                loadingImageBlocksRef.current.delete(block.id);
                if (!isMountedRef.current) return;
                if (loadingImageBlocksRef.current.size === 0) {
                    handleChange(editor, false);
                }
            };
            imgEl.addEventListener('load', onSettled, { once: true });
            imgEl.addEventListener('error', onSettled, { once: true });
        });
    };

    const handleChange = async (editor: BlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>, submitNeeded: boolean = true) => {
        registerImageLoadListeners(editor);
        form.setValue("messageDraftBody", JSON.stringify(editor.document), { shouldDirty: true });

        // Detect inline image blocks that were removed since the last change
        // and delete their corresponding attachment. If no attachment matches
        // (e.g. forwarded inline images that are never in the editor), it's a noop.
        const imageBlocks = editor.document.filter(block => block.type === 'image');
        const currentImageUrls = new Set(imageBlocks.map(img => img.props.url).filter(Boolean));

        Array.from(prevImageUrlsRef.current)
            .filter(url => !currentImageUrls.has(url))
            .forEach(removedUrl => {
                const attachment = attachmentsRef.current.find(
                    (a): a is Attachment => 'blobId' in a && !!a.cid && removedUrl.includes(a.blobId),
                );
                if (attachment) removeInlineImage(attachment.blobId);
            });
        prevImageUrlsRef.current = currentImageUrls;

        // Update signatureId
        const signatureBlock = editor.getBlock('signature');
        const signatureId = (signatureBlock?.type === 'signature' ? signatureBlock.props.templateId : undefined);
        form.setValue("signatureId", signatureId);

        // If signature block has changed, fire update immediately
        if (submitNeeded && signatureId !== draft?.signature?.id) {
            submitDraft?.();
        }
    }

    /**
     * Process the html and text content of the message when the editor is mounted.
     */
    useEffect(() => {
        editorRef.current = editor;
        if (!editor) return;
        handleChange(editor, false);
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
            // Insert the signature without forcing a draft to exist: an empty
            // new message must not create a draft just because a default
            // signature applies. The block's `messageId` is patched later by
            // the effect below once the user enters content and a draft is born,
            // which is when placeholders get resolved.
            const signatureBlock = {
                id: "signature",
                type: "signature" as const,
                props: {
                    templateId: signatureToUse.id,
                    mailboxId: mailboxId,
                    messageId: draft?.id,
                }
            };

            // Put signature at the end of the document or before the quote block if it exists
            MessageComposerHelper.insertSignatureBlock(editor, signatureBlock);

            // Set the signatureId in the form
            form.setValue('signatureId', signatureToUse.id);
        } else {
            // Set signatureId to undefined after a microtask to avoid flushSync issues
            form.setValue('signatureId', undefined);
        }
    }, [editor, isLoadingSignatures, activeSignatures, draft?.signature?.id]);

    // When a draft is created after the signature block was inserted,
    // update the block's messageId so placeholders can be resolved.
    useEffect(() => {
        if (!editor || !draft?.id) return;
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            const blockProps = signatureBlock.props as BlockSignatureConfigProps;
            if (blockProps.messageId !== draft.id) {
                editor.updateBlock('signature', {
                    props: { messageId: draft.id }
                });
            }
        }
    }, [editor, draft?.id]);

    // Sync direction: attachments → editor.
    // Removes image blocks whose attachment was deleted externally (e.g. via AttachmentUploader).
    // The reverse direction (editor → attachments) lives in handleChange above.
    // No loop occurs because:
    //  - This effect removing a block triggers handleChange, but the attachment is already gone
    //    from attachmentsRef so handleChange finds nothing to remove.
    //  - handleChange calling removeInlineImage returns the same array ref (via guard)
    //    when the attachment is already absent, so this effect doesn't re-fire.
    useEffect(() => {
        if (!editor) return;
        const inlineImages = editor.document.filter(block => block.type === 'image');
        if (inlineImages.length === 0) return;
        const blobAttachments = attachments.filter((a): a is Attachment => 'blobId' in a);
        const inlineImagesToRemove = inlineImages.filter(image =>
            editor.getBlock(image.id) && !blobAttachments.some(a => image.props.url.includes(a.blobId)),
        );
        if (inlineImagesToRemove.length > 0) {
            editor.removeBlocks(inlineImagesToRemove.map(image => image.id));
        }
    }, [attachments]);

    useEffect(() => () => { isMountedRef.current = false; }, []);

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
                        messageId={draft?.id}
                        ensureDraft={ensureDraft}
                        uploadInlineImage={uploadInlineImage}
                    />
                    <SignatureTemplateSelector
                        templates={activeSignatures}
                        isLoading={isLoadingSignatures}
                        mailboxId={mailboxId}
                        messageId={draft?.id}
                        defaultSelected={draft?.signature?.id}
                    />
                </Toolbar>
            </BlockNoteViewField>
            <input {...form.register("messageDraftBody")} type="hidden" />
            <input {...form.register("signatureId")} type="hidden" />
        </>
    );
});

MessageComposer.displayName = 'MessageComposer';
