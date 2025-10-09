import { BasicTextStyleButton, BlockTypeSelect, CreateLinkButton, FormattingToolbar } from "@blocknote/react";

type ToolbarProps = {
    children?: React.ReactNode;
}
export const Toolbar = ({ children }: ToolbarProps) => {
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
            <CreateLinkButton key={"createLinkButton"} />
            {children}
        </FormattingToolbar>
    )
}
