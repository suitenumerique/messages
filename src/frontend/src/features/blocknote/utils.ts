import * as locales from '@blocknote/core/locales';
import { Block } from '@blocknote/core';
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

/**
 * Block types to hide from the slash menu and BlockTypeSelect.
 * These blocks remain in the schema for backward-compatibility
 * (existing drafts may contain them) but are hidden from the UI.
 */
export const HIDDEN_BLOCK_TYPES = new Set([
    'toggleListItem',
    'file',
    'video',
    'audio',
    'table',
]);

/**
 * Returns true if a BlockTypeSelect item should be hidden.
 * Toggle headings share `type: "heading"` with normal headings
 * but have `props.isToggleable: true`, so we need to check props too.
 */
export const isHiddenBlockTypeSelectItem = (item: {
    type: string;
    props?: Record<string, unknown>;
}): boolean => {
    if (HIDDEN_BLOCK_TYPES.has(item.type)) return true;
    if (item.type === 'heading' && item.props?.isToggleable) return true;
    return false;
};

/**
 * Replaces `template-variable` inline content nodes with plain text
 * using resolved placeholder values. Recurses into children blocks.
 */
export const resolveTemplateVariables = (
    blocks: Block[],
    resolvedValues: Record<string, string>,
): Block[] => {
    return blocks.map((block) => {
        const resolvedBlock = { ...block };

        if (Array.isArray(block.content)) {
            resolvedBlock.content = block.content.flatMap(
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                (ic: any) => {
                    if (ic.type === 'template-variable') {
                        const value = resolvedValues[ic.props?.value] ?? `{${ic.props?.value}}`;
                        return { type: 'text' as const, text: value, styles: {} };
                    }
                    return ic;
                },
            );
        }

        if (Array.isArray(block.children) && block.children.length > 0) {
            resolvedBlock.children = resolveTemplateVariables(block.children, resolvedValues);
        }

        return resolvedBlock;
    });
};
