import type {
    BlockNoteEditor,
    BlockSchema,
    InlineContentSchema,
    PartialBlock,
    StyleSchema,
} from '@blocknote/core';

/**
 * Serializes BlockNote blocks to plain-text markdown for the email text body.
 *
 * Thin wrapper over BlockNote's `blocksToMarkdownLossy` that trims surrounding
 * whitespace. Since BlockNote >=0.51 it emits a trailing "\n" even for an empty
 * document; trimming preserves our contract of returning '' for empty content.
 */
export const blocksToMarkdown = async <
    BSchema extends BlockSchema,
    ISchema extends InlineContentSchema,
    SSchema extends StyleSchema,
>(
    editor: BlockNoteEditor<BSchema, ISchema, SSchema>,
    blocks: PartialBlock<BSchema, ISchema, SSchema>[],
): Promise<string> => {
    const markdown = await editor.blocksToMarkdownLossy(blocks);
    return markdown.trim();
};
