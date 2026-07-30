import { useBlockNoteEditor, useEditorState } from "@blocknote/react";
import { Icon, IconSize } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { RiBold, RiItalic, RiStrikethrough, RiUnderline } from "react-icons/ri";

import {
    createColumnListBlock,
    isInsideColumn,
} from "../column-layout-block/column-layout-insert-button";
import { cursorHasInlineContent, insertImageBlock } from "../image-upload-button";
import { ToolbarSeparator } from "../toolbar-separator";
import {
    BlockTypeButton,
    getEffectiveStyles,
    MobileToolbarButton,
    StyleToggleButton,
    TextAlignMobileButton,
    useAvailableBlockTypeItems,
} from "./buttons";

const SWATCH_COLORS = [
    "default",
    "gray",
    "brown",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
] as const;

type SwatchColor = (typeof SWATCH_COLORS)[number];
type SwatchStyleType = "textColor" | "backgroundColor";

const ColorSwatchButton = ({
    styleType,
    color,
}: {
    styleType: SwatchStyleType;
    color: SwatchColor;
}) => {
    const editor = useBlockNoteEditor();
    const activeColor = useEditorState({
        editor,
        selector: ({ editor }) =>
            getEffectiveStyles(editor)[styleType] ?? "default",
    });
    const title =
        styleType === "textColor"
            ? editor.dictionary.color_picker.text_title
            : editor.dictionary.color_picker.background_title;
    const dataKey =
        styleType === "textColor"
            ? 'data-text-color'
            : 'data-background-color';
    // Split objects (not a computed key) so each branch keeps the Styles
    // typing.
    const styles =
        styleType === "textColor"
            ? { textColor: color }
            : { backgroundColor: color };

    return (
        <button
            type="button"
            className={clsx("mobile-toolbar__swatch", {
                "mobile-toolbar__swatch--active": activeColor === color,
            })}
            {...{ [dataKey]: color }}
            data-style-type={styleType}
            data-value={color}
            aria-label={`${title}: ${editor.dictionary.color_picker.colors[color]}`}
            aria-pressed={activeColor === color}
            onClick={() => {
                if (color === "default") editor.removeStyles(styles);
                else editor.addStyles(styles);
            }}
        >
            A
        </button>
    );
};

type MobileFormatPanelProps = {
    /** Called after an insertion so the toolbar can fold the panel away. */
    onInsert: () => void;
};

/**
 * The progressive-disclosure panel opened by the "Aa" toolbar button:
 * block types, strike, alignment, insertions and text colors.
 */
export const MobileFormatPanel = ({ onInsert }: MobileFormatPanelProps) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();
    const availableBlockItems = useAvailableBlockTypeItems();
    // Headings last: paragraph, lists and quote are the frequent picks on
    // mobile, so they come first in the scroller.
    const blockItems = useMemo(
        () => [
            ...availableBlockItems.filter((item) => item.type !== "heading"),
            ...availableBlockItems.filter((item) => item.type === "heading"),
        ],
        [availableBlockItems],
    );

    const canInsertImage = useEditorState({
        editor,
        selector: ({ editor }) =>
            "image" in editor.schema.blockSpecs && cursorHasInlineContent(editor),
    });
    const canInsertColumns = useEditorState({
        editor,
        selector: ({ editor }) =>
            "columnList" in editor.schema.blockSpecs && !isInsideColumn(editor),
    });

    // The block-type list can overflow: when the panel opens, bring the
    // active item into view ("nearest" leaves the scroller alone when it is
    // already visible).
    const blockListRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        blockListRef.current
            ?.querySelector('[aria-pressed="true"]')
            ?.scrollIntoView({ block: "nearest", inline: "nearest" });
    }, []);

    return (
        <div className="mobile-toolbar__panel">
            <div className="mobile-toolbar__panel-inline" ref={blockListRef}>
                {blockItems.map((item) => (
                    <BlockTypeButton
                        key={`${item.type}-${item.props?.level ?? ""}`}
                        item={item}
                        showText
                    />
                ))}
            </div>
            <div className="mobile-toolbar__panel-row">
                {/* Bold/italic/underline are repeated from the buttons row:
                    that row folds away while this panel is open. */}
                <StyleToggleButton style="bold" icon={<RiBold size={20} />} />
                <StyleToggleButton style="italic" icon={<RiItalic size={20} />} />
                <StyleToggleButton
                    style="underline"
                    icon={<RiUnderline size={20} />}
                />
                <StyleToggleButton
                    style="strike"
                    icon={<RiStrikethrough size={20} />}
                />
                <ToolbarSeparator />
                {canInsertImage && (
                    <MobileToolbarButton
                        icon={<Icon name="image" size={IconSize.MEDIUM} />}
                        label={t("Insert image")}
                        onClick={() => {
                            insertImageBlock(editor);
                            onInsert();
                        }}
                    />
                )}
                {canInsertColumns && (
                    <MobileToolbarButton
                        icon={
                            <Icon
                                name="vertical_split"
                                size={IconSize.MEDIUM}
                                style={{ transform: "rotate(180deg)" }}
                            />
                        }
                        label={t("Insert 2 columns")}
                        onClick={() => {
                            const currentBlock = editor.getTextCursorPosition().block;
                            editor.insertBlocks(
                                [createColumnListBlock()],
                                currentBlock,
                                "after",
                            );
                            onInsert();
                        }}
                    />
                )}
                {(canInsertImage || canInsertColumns) && <ToolbarSeparator />}
                <TextAlignMobileButton alignment="left" />
                <TextAlignMobileButton alignment="center" />
                <TextAlignMobileButton alignment="right" />
            </div>
            {"textColor" in editor.schema.styleSchema && (
                <div className="mobile-toolbar__panel-row mobile-toolbar__panel-colors">
                    {SWATCH_COLORS.map((color) => (
                        <ColorSwatchButton
                            key={color}
                            styleType="textColor"
                            color={color}
                        />
                    ))}
                </div>
            )}
            {"backgroundColor" in editor.schema.styleSchema && (
                <div className="mobile-toolbar__panel-row mobile-toolbar__panel-colors">
                    {SWATCH_COLORS.map((color) => (
                        <ColorSwatchButton
                            key={color}
                            styleType="backgroundColor"
                            color={color}
                        />
                    ))}
                </div>
            )}
        </div>
    );
};
