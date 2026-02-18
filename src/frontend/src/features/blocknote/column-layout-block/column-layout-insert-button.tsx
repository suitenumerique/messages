import { useCallback } from 'react';
import { BlockNoteEditor } from '@blocknote/core';
import { useBlockNoteEditor, useComponentsContext, useEditorState } from '@blocknote/react';
import { useTranslation } from 'react-i18next';
import { Icon, IconSize } from '@gouvfr-lasuite/ui-kit';

// ---------------------------------------------------------------------------
// Column list block factory
// ---------------------------------------------------------------------------

export function createColumnListBlock() {
    return {
        type: 'columnList' as never,
        children: [
            {
                type: 'column' as never,
                props: { width: 0 } as never,
                children: [{ type: 'image' as never }],
            },
            {
                type: 'column' as never,
                props: { width: 2 } as never,
                children: [{ type: 'paragraph' as never }],
            },
        ],
    };
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/**
 * Check whether the current cursor position is inside a column node
 * by walking up the ProseMirror resolved position ancestors.
 */
export function isInsideColumn(editor: BlockNoteEditor) {
    const pmState = editor.prosemirrorState;
    const $from = pmState.selection.$from;
    for (let depth = $from.depth; depth >= 0; depth--) {
        if ($from.node(depth).type.name === 'column') return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Toolbar insert button
// ---------------------------------------------------------------------------

export const ColumnLayoutInsertButton = () => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();
    const Components = useComponentsContext();

    const insideColumn = useEditorState({
        editor,
        selector: (ctx) => isInsideColumn(ctx.editor as BlockNoteEditor),
    });

    const insertColumnLayout = useCallback(() => {
        if (insideColumn) return;
        const currentBlock = editor.getTextCursorPosition().block;
        editor.insertBlocks([createColumnListBlock()], currentBlock, 'after');
    }, [editor, insideColumn]);

    if (!Components) return null;
    if (!('columnList' in editor.schema.blockSpecs)) return null;
    if (insideColumn) return null;

    return (
        <Components.FormattingToolbar.Button
            icon={<Icon name="vertical_split" size={IconSize.SMALL} style={{ transform: 'rotate(180deg)' }} />}
            label={t('Insert 2 columns')}
            mainTooltip={t('Insert 2 columns')}
            isDisabled={insideColumn}
            onClick={insertColumnLayout}
        />
    );
};
