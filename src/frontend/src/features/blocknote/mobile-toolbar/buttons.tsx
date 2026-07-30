import {
    blockHasType,
    defaultProps,
    DefaultStyleSchema,
    editorHasBlockWithType,
    Styles,
    StyleSchema,
} from "@blocknote/core";
import {
    BlockTypeSelectItem,
    blockTypeSelectItems,
    useBlockNoteEditor,
    useEditorState,
} from "@blocknote/react";
import clsx from "clsx";
import { useMemo } from "react";
import { RiAlignCenter, RiAlignLeft, RiAlignRight } from "react-icons/ri";

import { isHiddenBlockTypeSelectItem } from "../utils";
import { ComposerEditor } from "./hooks";

type MobileToolbarButtonProps = {
    icon: React.ReactNode;
    label: string;
    /** Visible text under the icon (format panel grid). */
    text?: string;
    isActive?: boolean;
    isDisabled?: boolean;
    onClick: () => void;
};

export const MobileToolbarButton = ({
    icon,
    label,
    text,
    isActive,
    isDisabled,
    onClick,
}: MobileToolbarButtonProps) => (
    <button
        type="button"
        className={clsx("mobile-toolbar__button", {
            "mobile-toolbar__button--active": isActive,
        })}
        aria-label={label}
        aria-pressed={isActive}
        disabled={isDisabled}
        onClick={onClick}
    >
        {icon}
        {text && <span className="mobile-toolbar__button-text">{text}</span>}
    </button>
);

/**
 * Like editor.getActiveStyles(), but aware of ProseMirror's storedMarks —
 * the pending marks a toggle sets on a collapsed cursor. getActiveStyles only
 * reads the marks of the text at the cursor, so toggling bold with nothing
 * selected would leave the button visually inert until text is typed. When
 * storedMarks is set it IS the full mark set for the next insertion
 * (initialized from the cursor marks, then toggled), so it fully replaces
 * getActiveStyles' result rather than merging with it.
 */
export const getEffectiveStyles = (
    editor: ComposerEditor,
): Styles<DefaultStyleSchema> => {
    const storedMarks = editor._tiptapEditor.state.storedMarks;
    if (!storedMarks) return editor.getActiveStyles();

    const styles: Record<string, boolean | string> = {};
    // Widened to the base StyleSchema so marks can be looked up by name.
    const styleSchema: StyleSchema = editor.schema.styleSchema;
    for (const mark of storedMarks) {
        const config = styleSchema[mark.type.name];
        if (!config) continue;
        styles[config.type] =
            config.propSchema === "boolean" ? true : mark.attrs.stringValue;
    }
    return styles as Styles<DefaultStyleSchema>;
};

const STYLE_TOGGLES = {
    bold: { bold: true },
    italic: { italic: true },
    underline: { underline: true },
    strike: { strike: true },
} as const satisfies Record<string, Styles<DefaultStyleSchema>>;

export type BasicStyle = keyof typeof STYLE_TOGGLES;

export const StyleToggleButton = ({
    style,
    icon,
}: {
    style: BasicStyle;
    icon: React.ReactNode;
}) => {
    const editor = useBlockNoteEditor();
    const isActive = useEditorState({
        editor,
        selector: ({ editor }) => style in getEffectiveStyles(editor),
    });

    if (!(style in editor.schema.styleSchema)) return null;

    return (
        <MobileToolbarButton
            icon={icon}
            label={editor.dictionary.formatting_toolbar[style].tooltip}
            isActive={isActive}
            // No editor.focus() in these handlers: in the bar the capture-
            // phase mousedown guard already kept the editor focused, and in
            // the format panel the keyboard is deliberately dismissed —
            // commands apply to the state selection without DOM focus.
            onClick={() => editor.toggleStyles(STYLE_TOGGLES[style])}
        />
    );
};

/**
 * The block-type items available in this composer's schema, minus the ones
 * hidden from the UI (same filtering as the desktop BlockTypeSelect).
 */
export const useAvailableBlockTypeItems = (): BlockTypeSelectItem[] => {
    const editor = useBlockNoteEditor();
    return useMemo(
        () =>
            blockTypeSelectItems(editor.dictionary)
                .filter((item) => !isHiddenBlockTypeSelectItem(item))
                .filter((item) =>
                    editorHasBlockWithType(
                        editor,
                        item.type,
                        Object.fromEntries(
                            Object.entries(item.props || {}).map(
                                ([name, value]) => [name, typeof value],
                            ),
                        ) as Record<string, "string" | "number" | "boolean">,
                    ),
                ),
        [editor],
    );
};

export const BlockTypeButton = ({
    item,
    showText,
}: {
    item: BlockTypeSelectItem;
    showText?: boolean;
}) => {
    const editor = useBlockNoteEditor();
    const selectedBlocks = useEditorState({
        editor,
        selector: ({ editor }) =>
            editor.getSelection()?.blocks || [
                editor.getTextCursorPosition().block,
            ],
    });
    const firstBlock = selectedBlocks[0];
    const isSelected =
        firstBlock.type === item.type &&
        Object.entries(item.props || {}).every(
            ([name, value]) =>
                (firstBlock.props as Record<string, unknown>)[name] === value,
        );
    const ItemIcon = item.icon;

    return (
        <MobileToolbarButton
            icon={<ItemIcon size={20} />}
            label={item.name}
            text={showText ? item.name : undefined}
            isActive={isSelected}
            onClick={() => {
                editor.transact(() => {
                    // Re-tapping the active type toggles back to paragraph.
                    // `never` casts because BlockTypeSelectItem carries plain
                    // strings — same coercion the codebase uses for custom
                    // block types (see createColumnListBlock).
                    const update = isSelected
                        ? { type: "paragraph" as const }
                        : { type: item.type as never, props: item.props as never };
                    for (const block of selectedBlocks) {
                        editor.updateBlock(block, update);
                    }
                });
            }}
        />
    );
};

const ALIGNMENT_ICONS = {
    left: RiAlignLeft,
    center: RiAlignCenter,
    right: RiAlignRight,
} as const;

export type MobileTextAlignment = keyof typeof ALIGNMENT_ICONS;

export const TextAlignMobileButton = ({
    alignment,
}: {
    alignment: MobileTextAlignment;
}) => {
    const editor = useBlockNoteEditor();
    // Same detection as the desktop TextAlignButton, without its table
    // handling: table blocks are hidden in this app (see HIDDEN_BLOCK_TYPES).
    const state = useEditorState({
        editor,
        selector: ({ editor }) => {
            const selectedBlocks = editor.getSelection()?.blocks || [
                editor.getTextCursorPosition().block,
            ];
            const firstBlock = selectedBlocks[0];
            if (
                !blockHasType(firstBlock, editor, firstBlock.type, {
                    textAlignment: defaultProps.textAlignment,
                })
            ) {
                return undefined;
            }
            return {
                textAlignment: (firstBlock.props as Record<string, unknown>)
                    .textAlignment as string,
                blocks: selectedBlocks,
            };
        },
    });

    if (!state) return null;

    const AlignIcon = ALIGNMENT_ICONS[alignment];
    return (
        <MobileToolbarButton
            icon={<AlignIcon size={20} />}
            label={editor.dictionary.formatting_toolbar[`align_${alignment}`].tooltip}
            isActive={state.textAlignment === alignment}
            onClick={() => {
                editor.transact(() => {
                    for (const block of state.blocks) {
                        if (
                            blockHasType(block, editor, block.type, {
                                textAlignment: defaultProps.textAlignment,
                            })
                        ) {
                            editor.updateBlock(block, {
                                props: { textAlignment: alignment },
                            });
                        }
                    }
                });
            }}
        />
    );
};
