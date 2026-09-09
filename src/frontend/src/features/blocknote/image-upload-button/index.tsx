import { BlockNoteEditor, DefaultBlockSchema, DefaultInlineContentSchema, DefaultStyleSchema } from "@blocknote/core";
import { useBlockNoteEditor, useComponentsContext, useEditorState } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { Icon, IconSize } from "@gouvfr-lasuite/ui-kit";

// Default-schema typing, matching what `useBlockNoteEditor()` returns without
// explicit generics — the composers' extended schemas degrade to this.
type Editor = BlockNoteEditor<
    DefaultBlockSchema,
    DefaultInlineContentSchema,
    DefaultStyleSchema
>;

/**
 * True when the selection sits on blocks that can hold inline content —
 * i.e. an image block can be inserted after them.
 */
export const cursorHasInlineContent = (editor: Editor) => {
    const selectedBlocks = editor.getSelection()?.blocks || [
        editor.getTextCursorPosition().block,
    ];
    return selectedBlocks.some((block) => block.content !== undefined);
};

/**
 * Inserts an empty image block after the current one and opens the file
 * panel on it. Shared between the desktop toolbar button and the mobile
 * format panel.
 */
export const insertImageBlock = (editor: Editor) => {
    const currentBlock = editor.getTextCursorPosition().block;
    const insertedBlocks = editor.insertBlocks(
        [{ type: "image" }],
        currentBlock,
        "after",
    );
    const filePanel = editor.getExtension("filePanel") as { showMenu: (blockId: string) => void } | undefined;
    filePanel?.showMenu(insertedBlocks[0].id);
};

export const ImageUploadButton = () => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();
    const Components = useComponentsContext()!;

    const hasInlineContent = useEditorState({
        editor,
        selector: ({ editor }) => cursorHasInlineContent(editor),
    });

    if (!hasInlineContent) return null;

    return (
        <Components.FormattingToolbar.Button
            icon={<Icon name="image" size={IconSize.SMALL} />}
            label={t("Insert image")}
            mainTooltip={t("Insert image")}
            onClick={() => insertImageBlock(editor)}
        />
    );
};
