import * as locales from '@blocknote/core/locales';
import { Block, BlockNoteEditor, BlockSchema, InlineContentSchema, StyleSchema, defaultBlockSpecs } from '@blocknote/core';
import { TFunction } from 'i18next';
import { ALLOWED_IMAGE_MIME_TYPES } from '@/features/blocknote/image-block';
import { TEMPLATE_VARIABLE_TYPE } from '@/features/blocknote/inline-template-variable';
import { isNativePlatform } from '@/features/native/platform';

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
 * Bubbling custom event re-emitted from the tapped link when the native app
 * suppresses BlockNote's open-on-click (see createNativeLinkOptions).
 */
export const NATIVE_LINK_TAP_EVENT = 'blocknote:native-link-tap';

/**
 * `links` editor options for the native app: BlockNote's built-in click
 * handler `window.open`s the href from a ProseMirror `handleClick`, so no
 * DOM-level `preventDefault` can stop it — supplying `links.onClick` is the
 * documented way to disable it. The tap is re-emitted as a custom event so
 * the mobile toolbar can turn it into an edit action (see useEditLinkOnTap);
 * ProseMirror's `handleClick` fires on the simulated mouseup, which touch
 * guarantees, unlike the synthesized DOM click.
 *
 * ProseMirror skips its own caret placement when a click handler consumes
 * the event, so the caret is placed here from the tap coordinates — the
 * link editor reads the URL to edit from the selection.
 */
export const createNativeLinkOptions = () =>
    isNativePlatform()
        ? {
              links: {
                  onClick: (
                      event: MouseEvent,
                      editor: BlockNoteEditor<
                          BlockSchema,
                          InlineContentSchema,
                          StyleSchema
                      >,
                  ) => {
                      const tapped = editor.prosemirrorView?.posAtCoords({
                          left: event.clientX,
                          top: event.clientY,
                      });
                      if (tapped) {
                          editor._tiptapEditor.commands.setTextSelection(tapped.pos);
                      }
                      event.target?.dispatchEvent(
                          new CustomEvent(NATIVE_LINK_TAP_EVENT, { bubbles: true }),
                      );
                      return true;
                  },
              },
          }
        : {};

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

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const { video, audio, file, ...supportedBlockSpecs } = defaultBlockSpecs;

/**
 * The default BlockNote block specs, minus the media and file blocks.
 *
 * A `<video>` / `<audio>` / `<embed>` tag in pasted HTML used to create a
 * block: the editor displayed it, but the email exporter has no branch for
 * those types, so the content silently disappeared from the sent message. Out
 * of the schema, the pasted tag now yields no block at all.
 *
 * Files reach the composer through the clipboard/drop handlers instead, which
 * route anything that is not an image to the attachments — a path that never
 * goes through this schema.
 */
export const SUPPORTED_BLOCK_SPECS = supportedBlockSpecs;

/**
 * Block types to hide from the slash menu and BlockTypeSelect.
 * These blocks remain in the schema for backward-compatibility
 * (existing drafts may contain them) but are hidden from the UI.
 *
 * Video, audio and file are not listed here: they are out of the schemas
 * entirely, see {@link SUPPORTED_BLOCK_SPECS}.
 */
export const HIDDEN_BLOCK_TYPES = new Set([
    'toggleListItem',
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
 * Removes the blocks whose type is not part of the editor schema.
 *
 * Video, audio and file blocks were reachable by pasting HTML carrying a
 * `<video>` / `<audio>` / `<embed>` tag: the editor displayed them, but the
 * email exporter has no branch for them, so they silently vanished from the
 * sent message. They are now out of the schemas — which makes BlockNote throw
 * on any draft saved back then, since it cannot build a document from a type it
 * does not know. Dropping them here keeps those drafts openable.
 *
 * Operates on the raw JSON blocks (pre-`useCreateBlockNote`), hence the loose
 * typing. Recurses into children blocks.
 *
 * @param blocks - the parsed draft content
 * @param supportedTypes - the block types the schema declares
 */
export const dropUnsupportedBlocks = (
    blocks: Record<string, unknown>[],
    supportedTypes: string[],
): Record<string, unknown>[] =>
    blocks
        // A block without a type is a paragraph as far as BlockNote is concerned.
        .filter((block) => typeof block.type !== 'string' || supportedTypes.includes(block.type))
        .map((block) => {
            const children = block.children;
            if (!Array.isArray(children) || children.length === 0) return block;
            return {
                ...block,
                children: dropUnsupportedBlocks(
                    children as Record<string, unknown>[],
                    supportedTypes,
                ),
            };
        });

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
