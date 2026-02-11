import { BlockNoteViewField } from "@/features/blocknote/blocknote-view-field";
import { BlockNoteEditor, BlockNoteEditorOptions, BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs, PartialBlock } from "@blocknote/core";
import { filterSuggestionItems } from "@blocknote/core/extensions";
import * as locales from '@blocknote/core/locales';
import { SuggestionMenuController, useCreateBlockNote } from "@blocknote/react";
import { FieldProps } from "@gouvfr-lasuite/cunningham-react";
import { useCallback, useEffect, useMemo } from "react";
import { useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { InlineTemplateVariable, TemplateVariableSelector } from "@/features/blocknote/inline-template-variable";
import { Toolbar } from "@/features/blocknote/toolbar";
import { usePlaceholdersRetrieve } from "@/features/api/gen";
import MailHelper from "@/features/utils/mail-helper";
import { imageBlockSpec, ALLOWED_IMAGE_MIME_TYPES } from "@/features/blocknote/image-block";
import { useUploadImageAsBase64 } from "@/features/blocknote/image-block/use-upload-image-as-base64";
import { useImageObjectUrls } from "@/features/blocknote/image-block/use-image-object-urls";
import { ImageUploadButton } from "@/features/blocknote/image-upload-button";
import { useConfig } from "@/features/providers/config";

const SIGNATURE_BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'image': imageBlockSpec,
    },
    inlineContentSpecs: {
        ...defaultInlineContentSpecs,
        'template-variable': InlineTemplateVariable,
    }
});

export type SignatureComposerBlockNoteSchema = typeof SIGNATURE_BLOCKNOTE_SCHEMA;
export type SignatureComposerBlockSchema = SignatureComposerBlockNoteSchema['blockSchema'];
export type SignatureComposerInlineContentSchema = SignatureComposerBlockNoteSchema['inlineContentSchema'];
export type SignatureComposerStyleSchema = SignatureComposerBlockNoteSchema['styleSchema'];
export type PartialSignatureComposerBlockSchema = PartialBlock<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>;

type SignatureComposerProps = FieldProps & {
    blockNoteOptions?: Partial<BlockNoteEditorOptions<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>>,
    defaultValue?: string | null;
    disabled?: boolean;
}

/**
 * Shared composer component for signature content.
 * Used by both admin (maildomain) and mailbox signature modals.
 */
export const SignatureComposer = ({ blockNoteOptions, defaultValue, disabled = false, ...props }: SignatureComposerProps) => {
    const { t, i18n } = useTranslation();
    const form = useFormContext();
    const config = useConfig();
    const baseUploadFile = useUploadImageAsBase64(config.MAX_TEMPLATE_IMAGE_SIZE);
    const { createObjectUrl, resolveObjectUrls } = useImageObjectUrls();

    const uploadFile = useCallback(async (file: File) => {
        const base64 = await baseUploadFile(file);
        if (base64 === '#') return '#';
        return createObjectUrl(file, base64);
    }, [baseUploadFile, createObjectUrl]);

    const { data: { data: placeholders = {} } = {}, isLoading: isLoadingPlaceholders } = usePlaceholdersRetrieve();
    const canShowPlaceholdersMenu = !isLoadingPlaceholders && !!placeholders;

    const initialContent = useMemo(() => {
        if (!defaultValue) return [{ type: "paragraph", content: "" }];
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
        schema: SIGNATURE_BLOCKNOTE_SCHEMA,
        tabBehavior: "prefer-navigate-ui",
        autofocus: "end",
        initialContent,
        trailingBlock: true,
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

    const getPlaceholderMenuItems = (editor: BlockNoteEditor<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>) => {
        return Object.entries(placeholders).map(([value, label]) => ({
            title: label,
            onItemClick: () => {
                editor.insertInlineContent([{ type: "template-variable", props: { value: value, label: label } }, " "]);
            }
        }));
    }


    useEffect(() => {
        handleChange();
    }, [])

    return (
        <>
            <BlockNoteViewField
                {...props}
                className="signature-composer"
                fullWidth
                disabled={disabled}
                composerProps={{
                    editor,
                    onChange: handleChange,
                }}
            >
                <Toolbar>
                    <ImageUploadButton />
                    {canShowPlaceholdersMenu &&
                        <TemplateVariableSelector key={"templateVariableSelector"} variables={placeholders} isLoading={isLoadingPlaceholders} />
                    }
                </Toolbar>
                {canShowPlaceholdersMenu &&
                    <SuggestionMenuController
                        triggerCharacter="{"
                        getItems={async (query) => filterSuggestionItems(getPlaceholderMenuItems(editor), query)}
                    />
                }
            </BlockNoteViewField>
            <input {...form.register("htmlBody")} type="hidden" />
            <input {...form.register("textBody")} type="hidden" />
            <input {...form.register("rawBody")} type="hidden" />
        </>
    )
};
