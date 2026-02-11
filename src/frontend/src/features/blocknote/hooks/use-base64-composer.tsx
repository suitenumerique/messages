import { BlockNoteSchema, BlockNoteEditorOptions, BlockSchemaFromSpecs, InlineContentSchemaFromSpecs, StyleSchemaFromSpecs, BlockSpecs, InlineContentSpecs, StyleSpecs, PartialBlock } from '@blocknote/core';
import { useCreateBlockNote } from '@blocknote/react';
import { Extension } from '@tiptap/core';
import { useCallback, useEffect, useMemo } from 'react';
import { useFormContext } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { useUploadImageAsBase64 } from '@/features/blocknote/image-block/use-upload-image-as-base64';
import { useImageObjectUrls } from '@/features/blocknote/image-block/use-image-object-urls';
import { useConfig } from '@/features/providers/config';
import MailHelper from '@/features/utils/mail-helper';
import { createBlockNoteDictionary, createNonImageFileBlockers, removeFailedImageBlocks } from '@/features/blocknote/utils';

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

    const uploadFile = useCallback(async (file: File) => {
        const base64 = await baseUploadFile(file);
        if (base64 === '#') return '#';
        return createObjectUrl(file, base64);
    }, [baseUploadFile, createObjectUrl]);

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

    const handleChange = useCallback(async () => {
        if (removeFailedImageBlocks(editor)) return;

        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        form.setValue("rawBody", resolveObjectUrls(JSON.stringify(editor.document)), { shouldDirty: true });
        form.setValue("textBody", resolveObjectUrls(markdown));
        form.setValue("htmlBody", resolveObjectUrls(html));
    }, [editor, form, resolveObjectUrls]);

    useEffect(() => {
        handleChange();
    }, []);

    return { editor, handleChange };
};
