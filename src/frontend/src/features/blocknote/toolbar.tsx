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

import { isNativePlatform } from "@/features/native/platform";
import { ColumnLayoutInsertButton } from "./column-layout-block/column-layout-insert-button";
import { ImageUploadButton } from "./image-upload-button";
import { MobileToolbar } from "./mobile-toolbar";
import { ToolbarSeparator } from "./toolbar-separator";
import { isHiddenBlockTypeSelectItem } from "./utils";

type ToolbarProps = {
    children?: React.ReactNode;
}

const DesktopToolbar = ({ children }: ToolbarProps) => {
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
            <CreateLinkButton key={"createLinkButton"} />
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

            {children && (
                <>
                    <ToolbarSeparator key={"separator-4"} />
                    {children}
                </>
            )}
        </FormattingToolbar>
    );
};

export const Toolbar = ({ children }: ToolbarProps) => {
    // The platform never changes at runtime, so branching to components with
    // different hooks is safe.
    if (isNativePlatform()) {
        return <MobileToolbar>{children}</MobileToolbar>;
    }
    return <DesktopToolbar>{children}</DesktopToolbar>;
}
