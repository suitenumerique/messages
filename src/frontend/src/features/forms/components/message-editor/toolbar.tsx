import { BasicTextStyleButton, BlockTypeSelect, CreateLinkButton, FormattingToolbar } from "@blocknote/react";
import { Button } from "@openfun/cunningham-react";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";

const MessageEditorToolbar = () => {
    const [reformulateDropdownIsOpen, setReformulateDropdownIsOpen] = useState(false);
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
            <DropdownMenu
            isOpen={reformulateDropdownIsOpen}
            onOpenChange={setReformulateDropdownIsOpen}
            options={[{
                label: "Reformuler poliment",
                value: "reformulate-politely",
            }]}>
                <Button icon={<span className="material-icons">auto_awesome</span>} size="small" color="tertiary-text" onClick={(event) => event.preventDefault()} />
            </DropdownMenu>
        </FormattingToolbar>
    )
}

export default MessageEditorToolbar;
