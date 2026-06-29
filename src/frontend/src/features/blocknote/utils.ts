import * as locales from '@blocknote/core/locales';
import { Block } from '@blocknote/core';
import { TFunction } from 'i18next';
import { ALLOWED_IMAGE_MIME_TYPES } from '@/features/blocknote/image-block';
import { TEMPLATE_VARIABLE_TYPE } from '@/features/blocknote/inline-template-variable';

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
                    if (ic.type === TEMPLATE_VARIABLE_TYPE) {
                        const value = resolvedValues[ic.props?.value] ?? `{${ic.props?.value}}`;
                        // Carry over the styles applied to the token so the
                        // resolved text keeps its bold/italic/color formatting.
                        const styles = ic.content?.[0]?.styles ?? {};
                        return { type: 'text' as const, text: value, styles };
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

/**
 * Backfills the styled `content` of legacy `template-variable` inline nodes.
 *
 * These tokens used to be stored with `content: "none"` (no styled content),
 * the slug being rendered from `props.value`. The inline spec now uses
 * `content: "styled"` and renders the token from its `content`, so a legacy
 * node with an empty `content` shows up as an empty blue chip. We seed the
 * missing content from the persisted `label` (falling back to the `value`
 * slug) so old signatures and templates keep displaying their variable names.
 *
 * Operates on the raw JSON blocks (pre-`useCreateBlockNote`), hence the loose
 * typing. Recurses into children blocks.
 */
export const backfillTemplateVariableContent = (
    blocks: Record<string, unknown>[],
): Record<string, unknown>[] => {
    return blocks.map((block) => {
        const result = { ...block };

        if (Array.isArray(result.content)) {
            result.content = result.content.map((ic: Record<string, unknown>) => {
                const isEmptyToken =
                    ic?.type === TEMPLATE_VARIABLE_TYPE &&
                    (!Array.isArray(ic.content) || ic.content.length === 0);
                if (!isEmptyToken) return ic;

                const props = (ic.props ?? {}) as Record<string, unknown>;
                const text = (props.label as string) || (props.value as string) || '';
                return { ...ic, content: [{ type: 'text', text, styles: {} }] };
            });
        }

        const children = result.children;
        if (Array.isArray(children) && children.length > 0) {
            result.children = backfillTemplateVariableContent(children as Record<string, unknown>[]);
        }

        return result;
    });
};
