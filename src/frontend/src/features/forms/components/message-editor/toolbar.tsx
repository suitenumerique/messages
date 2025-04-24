import { BasicTextStyleButton, BlockTypeSelect, CreateLinkButton, FormattingToolbar } from "@blocknote/react";

const MessageEditorToolbar = () => {
    return (
        <FormattingToolbar>
            <BlockTypeSelect key={"blockTypeSelect"} />
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
            <BasicTextStyleButton
                key={"codeStyleButton"}
                basicTextStyle={"code"}
            />
            <CreateLinkButton key={"createLinkButton"} />
        </FormattingToolbar>
    )
}

export default MessageEditorToolbar;
