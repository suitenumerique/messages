import {
    BasicTextStyleButton,
    blockTypeSelectItems,
    BlockTypeSelect,
    ColorStyleButton,
    CreateLinkButton,
    FileCaptionButton,
    FileDeleteButton,
    FilePreviewButton,
    FileReplaceButton,
    FormattingToolbar,
    TextAlignButton,
    useBlockNoteEditor,
} from "@blocknote/react";
import { useMemo } from "react";

import { ColumnLayoutInsertButton } from "./column-layout-block/column-layout-insert-button";
import { ImageUploadButton } from "./image-upload-button";
import { isHiddenBlockTypeSelectItem } from "./utils";

const ToolbarSeparator = () => (
    <div className="bn-toolbar-separator" role="separator" />
);

type ToolbarProps = {
    children?: React.ReactNode;
}
export const Toolbar = ({ children }: ToolbarProps) => {
    const editor = useBlockNoteEditor();
    const filteredItems = useMemo(
        () => blockTypeSelectItems(editor.dictionary).filter(
            (item) => !isHiddenBlockTypeSelectItem(item),
        ),
        [editor.dictionary],
    );

    return (
        <FormattingToolbar>
            <BlockTypeSelect key={"blockTypeSelect"} items={filteredItems} />
            <ImageUploadButton />
            <ColumnLayoutInsertButton />

            <ToolbarSeparator key={"separator-1"} />

            <FileCaptionButton key={"fileCaptionButton"} />
            <FileReplaceButton key={"fileReplaceButton"} />
            <FileDeleteButton key={"fileDeleteButton"} />
            <FilePreviewButton key={"filePreviewButton"} />
            <BasicTextStyleButton
                basicTextStyle={"bold"}
                key={"boldStyleButton"}
            />
            <BasicTextStyleButton
                basicTextStyle={"italic"}
                key={"italicStyleButton"}
            />
            <BasicTextStyleButton
                basicTextStyle={"underline"}
                key={"underlineStyleButton"}
            />
            <BasicTextStyleButton
                basicTextStyle={"strike"}
                key={"strikeStyleButton"}
            />

            <ToolbarSeparator key={"separator-2"} />

            <ColorStyleButton key={"colorStyleButton"} />

            <ToolbarSeparator key={"separator-3"} />

            <TextAlignButton textAlignment={"left"} key={"textAlignLeftButton"} />
            <TextAlignButton textAlignment={"center"} key={"textAlignCenterButton"} />
            <TextAlignButton textAlignment={"right"} key={"textAlignRightButton"} />

            <ToolbarSeparator key={"separator-4"} />

            <CreateLinkButton key={"createLinkButton"} />
            {children}
        </FormattingToolbar>
    )
}
