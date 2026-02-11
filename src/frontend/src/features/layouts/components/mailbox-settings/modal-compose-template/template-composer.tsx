import { BlockNoteViewField } from "@/features/blocknote/blocknote-view-field";
import { BlockNoteEditorOptions, BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs } from "@blocknote/core";
import { InlineTemplateVariable, TemplateVariableSelector } from "@/features/blocknote/inline-template-variable";
import * as locales from '@blocknote/core/locales';
import { useCreateBlockNote } from "@blocknote/react";
import { FieldProps } from "@gouvfr-lasuite/cunningham-react";
import { useEffect, useCallback, useMemo } from "react";
import { useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Toolbar } from "@/features/blocknote/toolbar";
import MailHelper from "@/features/utils/mail-helper";
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from "@/features/blocknote/signature-block";
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList, usePlaceholdersRetrieve } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { imageBlockSpec, ALLOWED_IMAGE_MIME_TYPES } from "@/features/blocknote/image-block";
import { useUploadImageAsBase64 } from "@/features/blocknote/image-block/use-upload-image-as-base64";
import { useImageObjectUrls } from "@/features/blocknote/image-block/use-image-object-urls";
import { ImageUploadButton } from "@/features/blocknote/image-upload-button";
import { useConfig } from "@/features/providers/config";
import { SmartTrailingBlock } from "@/features/blocknote/smart-trailing-block";

const TEMPLATE_BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'image': imageBlockSpec,
        'signature': BlockSignature(),
    },
    inlineContentSpecs: {
        ...defaultInlineContentSpecs,
        'template-variable': InlineTemplateVariable,
    }
});

export type TemplateComposerBlockNoteSchema = typeof TEMPLATE_BLOCKNOTE_SCHEMA;
export type TemplateComposerBlockSchema = TemplateComposerBlockNoteSchema['blockSchema'];
export type TemplateComposerInlineContentSchema = TemplateComposerBlockNoteSchema['inlineContentSchema'];
export type TemplateComposerStyleSchema = TemplateComposerBlockNoteSchema['styleSchema'];

type TemplateComposerProps = FieldProps & {
    blockNoteOptions?: Partial<BlockNoteEditorOptions<TemplateComposerBlockSchema, TemplateComposerInlineContentSchema, TemplateComposerStyleSchema>>,
    defaultValue?: string | null;
    disabled?: boolean;
}

/**
 * The composer component for the template content.
 */
export const TemplateComposer = ({ blockNoteOptions, defaultValue, disabled = false, ...props }: TemplateComposerProps) => {
    const { t, i18n } = useTranslation();
    const form = useFormContext();
    const { selectedMailbox } = useMailboxContext();
    const config = useConfig();
    const baseUploadFile = useUploadImageAsBase64(config.MAX_TEMPLATE_IMAGE_SIZE);
    const { createObjectUrl, resolveObjectUrls } = useImageObjectUrls();

    const uploadFile = useCallback(async (file: File) => {
        const base64 = await baseUploadFile(file);
        if (base64 === '#') return '#';
        return createObjectUrl(file, base64);
    }, [baseUploadFile, createObjectUrl]);

    const { data: { data: placeholders = {} } = {}, isLoading: isLoadingPlaceholders } = usePlaceholdersRetrieve({
        query: {
            refetchOnMount: true,
            refetchOnWindowFocus: true,
        }
    });

    const { data: { data: activeSignatures = [] } = {}, isLoading: isLoadingSignatures } = useMailboxesMessageTemplatesAvailableList(
        selectedMailbox?.id || "",
        {
            type: MessageTemplateTypeChoices.signature,
        },
        {
            query: {
                enabled: !!selectedMailbox?.id,
                refetchOnMount: true,
                refetchOnWindowFocus: true,
            },
        }
    );

    const initialContent = useMemo(() => {
        if (!defaultValue) return [{ type: "paragraph", content: [{ type: "text", text: "", styles: {} }] }];
        const blocks = JSON.parse(defaultValue);
        return blocks.map((block: Record<string, unknown>, i: number) => {
            const props = block.props as Record<string, string> | undefined;
            if (block.type === 'image' && props?.url?.startsWith('data:')) {
                const file = MailHelper.dataUrlToFile(props.url, `image-${i}.png`);
                if (file) {
                    return { ...block, props: { ...props, url: createObjectUrl(file, props.url) } };
                }
            }
            return block;
        });
    }, [defaultValue, createObjectUrl]);

    const locale = i18n.resolvedLanguage?.split('-')[0] || 'en';
    const editor = useCreateBlockNote({
        schema: TEMPLATE_BLOCKNOTE_SCHEMA,
        tabBehavior: "prefer-navigate-ui",
        initialContent,
        trailingBlock: false,
        uploadFile,
        dictionary: {
            ...(locales[locale as keyof typeof locales] || locales.en),
            placeholders: {
                ...(locales[locale as keyof typeof locales] || locales.en).placeholders,
                emptyDocument: t('Start typing...'),
                default: t('Start typing...'),
            }
        },
        ...blockNoteOptions,
        _tiptapOptions: {
            extensions: [SmartTrailingBlock],
            editorProps: {
                handleDOMEvents: {
                    drop: (_view: unknown, event: DragEvent) => {
                        const files = Array.from(event.dataTransfer?.files || []);
                        if (files.length === 0) return false;
                        const hasNonImage = files.some(f => !ALLOWED_IMAGE_MIME_TYPES.includes(f.type));
                        if (hasNonImage) {
                            event.preventDefault();
                            return true;
                        }
                        return false;
                    },
                    paste: (_view: unknown, event: ClipboardEvent) => {
                        const files = Array.from(event.clipboardData?.files || []);
                        if (files.length === 0) return false;
                        const hasNonImage = files.some(f => !ALLOWED_IMAGE_MIME_TYPES.includes(f.type));
                        if (hasNonImage) {
                            event.preventDefault();
                            return true;
                        }
                        return false;
                    },
                },
            },
        },
    }, [i18n.resolvedLanguage]);

    const handleChange = useCallback(async () => {
        // Remove image blocks whose upload failed (url is "#")
        const failedImageBlocks = editor.document.filter(
            (block) => block.type === 'image' && block.props.url === "#",
        );
        if (failedImageBlocks.length > 0) {
            editor.removeBlocks(failedImageBlocks.map((b) => b.id));
            return;
        }

        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        form.setValue("rawBody", resolveObjectUrls(JSON.stringify(editor.document)), { shouldDirty: true });
        form.setValue("textBody", resolveObjectUrls(markdown));
        form.setValue("htmlBody", resolveObjectUrls(html));
    }, [editor, form, resolveObjectUrls]);

    useEffect(() => {
        if(!editor) return;

        // Detect current signature on mount
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock?.type === 'signature') {
            const templateId = signatureBlock.props.templateId;
            const signature = activeSignatures.find(s => s.id === templateId);
            if (signature) {
                // Update the signature selector
                editor.updateBlock(signatureBlock.id, {
                    type: 'signature',
                    props: {
                        templateId: signature.id,
                        mailboxId: selectedMailbox?.id,
                    }
                });
            }
        }

        handleChange();
    }, [editor, handleChange, activeSignatures, selectedMailbox?.id]);

    useEffect(() => {
        if (!editor || isLoadingSignatures) return;

        // Check if signature is already in the editor
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            // In case there is a signature block, we remove the block if :
            // - the templateId does not match an active signature
            const blockSignatureId = (signatureBlock.props as BlockSignatureConfigProps).templateId;
            const isSignatureStale = activeSignatures.findIndex(signature => signature.id === blockSignatureId) < 0;
            if (isSignatureStale) editor.removeBlocks(["signature"]);
            else return;
        }

        if (activeSignatures.length === 0) return;

        let signatureToUse = undefined;

        // Use in priority the forced signature block if it exists
        signatureToUse = activeSignatures.find(signature => signature.is_forced);

        // Add signature block if we have a signature to use
        if (signatureToUse) {
            // Add signature at the end of the document
            const signatureBlock = {
                id: "signature",
                type: "signature" as const,
                props: {
                    templateId: signatureToUse.id,
                    mailboxId: selectedMailbox?.id,
                }
            };

            // Insert at the end
            if (editor.document.length === 0) {
                editor.insertBlocks([{ type: "paragraph", content: [{ type: "text", text: "", styles: {} }] }], "", "after");
            }

            // Put signature at the end of the document
            // Insert signature at the end of the document
            editor.insertBlocks([signatureBlock], editor.document[editor.document.length - 1].id, "after");

        }
    }, [editor, isLoadingSignatures, activeSignatures, selectedMailbox?.id]);

    return (
        <>
            <BlockNoteViewField
                {...props}
                className="template-composer"
                fullWidth
                disabled={disabled}
                composerProps={{
                    editor,
                    onChange: handleChange,
                }}
            >
                <Toolbar>
                    <ImageUploadButton />
                    <SignatureTemplateSelector
                        templates={activeSignatures}
                        isLoading={isLoadingSignatures}
                        mailboxId={selectedMailbox?.id}
                    />
                    <TemplateVariableSelector
                        variables={placeholders}
                        isLoading={isLoadingPlaceholders}
                    />
                </Toolbar>
            </BlockNoteViewField>
            <input {...form.register("htmlBody")} type="hidden" />
            <input {...form.register("textBody")} type="hidden" />
            <input {...form.register("rawBody")} type="hidden" />
        </>
    )
};
