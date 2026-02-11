import * as locales from '@blocknote/core/locales';
import { BlockNoteEditor } from '@blocknote/core';
import { TFunction } from 'i18next';
import { ALLOWED_IMAGE_MIME_TYPES } from '@/features/blocknote/image-block';

/**
 * Builds the BlockNote i18n dictionary for the given locale.
 */
export const createBlockNoteDictionary = (locale: string, t: TFunction) => ({
    ...(locales[locale as keyof typeof locales] || locales.en),
    placeholders: {
        ...(locales[locale as keyof typeof locales] || locales.en).placeholders,
        emptyDocument: t('Start typing...'),
        default: t('Start typing...'),
    },
});

/**
 * Removes image blocks whose upload failed (url is "#").
 * Returns true if blocks were removed, allowing the caller to bail out early.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const removeFailedImageBlocks = (editor: BlockNoteEditor<any, any, any>): boolean => {
    const failedImageBlocks = editor.document.filter(
        (block) => block.type === 'image' && block.props.url === '#',
    );
    if (failedImageBlocks.length > 0) {
        editor.removeBlocks(failedImageBlocks.map((b) => b.id));
        return true;
    }
    return false;
};

/**
 * Returns TipTap handleDOMEvents handlers that block non-image file
 * drops and pastes. Used by composers that only accept image uploads
 * (SignatureComposer, TemplateComposer).
 */
export const createNonImageFileBlockers = () => ({
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
});
