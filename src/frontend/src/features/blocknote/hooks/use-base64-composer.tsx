import { BlockNoteSchema, BlockNoteEditor, BlockNoteEditorOptions, BlockSchemaFromSpecs, InlineContentSchemaFromSpecs, StyleSchemaFromSpecs, BlockSpecs, InlineContentSpecs, StyleSpecs, PartialBlock } from '@blocknote/core';
import { useCreateBlockNote } from '@blocknote/react';
import { Extension } from '@tiptap/core';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useFormContext } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { useUploadImageAsBase64 } from '@/features/blocknote/image-block/use-upload-image-as-base64';
import { useImageObjectUrls } from '@/features/blocknote/image-block/use-image-object-urls';
import { EmailExporter } from '@/features/blocknote/email-exporter';
import { useConfig } from '@/features/providers/config';
import MailHelper from '@/features/utils/mail-helper';
import { createBlockNoteDictionary, createNonImageFileBlockers } from '@/features/blocknote/utils';
import { handle } from '@/features/utils/errors';

const emailExporter = new EmailExporter();

export type Base64ComposerHandle = {
    exportContent: () => Promise<{ htmlBody: string; textBody: string }>;
};

type UseBase64ComposerOptions<
    B extends BlockSpecs,
    I extends InlineContentSpecs,
    S extends StyleSpecs,
> = {
    schema: BlockNoteSchema<BlockSchemaFromSpecs<B>, InlineContentSchemaFromSpecs<I>, StyleSchemaFromSpecs<S>>;
    defaultValue?: string | null;
    blockNoteOptions?: Partial<BlockNoteEditorOptions<BlockSchemaFromSpecs<B>, InlineContentSchemaFromSpecs<I>, StyleSchemaFromSpecs<S>>>;
    trailingBlock?: boolean;
    extensions?: Extension[];
};

/**
 * Hook encapsulating the shared logic between SignatureComposer and
 * TemplateComposer: base64 image upload pipeline, initial content
 * parsing (data URLs to Object URLs), editor creation with i18n and
 * non-image file blockers, and form synchronisation on change.
 */
export const useBase64Composer = <
    B extends BlockSpecs,
    I extends InlineContentSpecs,
    S extends StyleSpecs,
>({
    schema,
    defaultValue,
    blockNoteOptions,
    trailingBlock = true,
    extensions,
}: UseBase64ComposerOptions<B, I, S>) => {
    const { t, i18n } = useTranslation();
    const form = useFormContext();
    const config = useConfig();
    const baseUploadFile = useUploadImageAsBase64(config.MAX_TEMPLATE_IMAGE_SIZE);
    const { createObjectUrl, resolveObjectUrls } = useImageObjectUrls();
    const editorRef = useRef<BlockNoteEditor<BlockSchemaFromSpecs<B>, InlineContentSchemaFromSpecs<I>, StyleSchemaFromSpecs<S>>>(null);

    const uploadFile = useCallback(async (file: File, blockId?: string) => {
        const base64 = await baseUploadFile(file);
        if (base64 === null) {
            if (blockId) {
                // Schedule removal after BlockNote's updateBlock completes.
                // We can't remove synchronously because updateBlock would
                // throw "Block not found", and we can't throw because
                // handleFileInsertion doesn't catch (unhandled rejection).
                setTimeout(() => editorRef.current?.removeBlocks([blockId]), 0);
            }
            return '';
        }
        return createObjectUrl(file, base64);
    }, [baseUploadFile, createObjectUrl]);

    const initialContent = useMemo(() => {
        const DEFAULT_CONTENT = [{ type: "paragraph", content: "" }];
        if (!defaultValue) return DEFAULT_CONTENT;
        let blocks = [];
        try {
            blocks = JSON.parse(defaultValue);
        } catch (error) {
            handle(new Error("Error parsing initial content."), { extra: { error, defaultValue } });
            return DEFAULT_CONTENT;
        }

        // Traverse blocks tree to transform image data URLs to Object URLs
        let imageIndex = 0;
        const processImageBlocks = (blocks: Record<string, unknown>[]) => {
            return blocks.map((block: Record<string, unknown>) => {
                let result = { ...block };
                const props = result.props as Record<string, string> | undefined;
                if (result.type === 'image' && props?.url?.startsWith('data:')) {
                    const file = MailHelper.dataUrlToFile(props.url, `image-${imageIndex}.png`);
                    imageIndex++;
                    if (file) {
                        return { ...block, props: { ...props, url: createObjectUrl(file, props.url) } };
                    }
                }

                const children = result.children as Record<string, unknown>[] | undefined;
                if (children?.length) {
                    result = { ...result, children: processImageBlocks(children) };
                }
                return result;
            })
        };

        return processImageBlocks(blocks);
    }, [defaultValue, createObjectUrl]);

    const locale = i18n.resolvedLanguage?.split('-')[0] || 'en';
    const nonImageFileBlockers = createNonImageFileBlockers();

    const editor = useCreateBlockNote({
        schema,
        tabBehavior: "prefer-navigate-ui",
        initialContent: initialContent as PartialBlock<BlockSchemaFromSpecs<B>, InlineContentSchemaFromSpecs<I>, StyleSchemaFromSpecs<S>>[],
        trailingBlock,
        uploadFile,
        dictionary: createBlockNoteDictionary(locale, t),
        ...blockNoteOptions,
        _tiptapOptions: {
            ...(extensions ? { extensions } : {}),
            editorProps: {
                handleDOMEvents: nonImageFileBlockers,
            },
        },
    }, [i18n.resolvedLanguage]);

    const handleChange = useCallback(() => {
        form.setValue("rawBody", resolveObjectUrls(JSON.stringify(editor.document)), { shouldDirty: true });
    }, [editor, form, resolveObjectUrls]);

    const exportContent = useCallback(async () => {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = emailExporter.exportBlocks(editor.document, editor.domElement ?? null);
        return {
            htmlBody: resolveObjectUrls(html),
            textBody: resolveObjectUrls(markdown),
        };
    }, [editor, resolveObjectUrls]);

    useEffect(() => {
        handleChange();
    }, []);

    useEffect(() => {
        editorRef.current = editor;
    }, [editor]);

    return { editor, handleChange, exportContent };
};
