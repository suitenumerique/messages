import { CustomBlockConfig } from "@blocknote/core";
import { createReactBlockSpec } from "@blocknote/react";

const blockConfig: CustomBlockConfig = {
    type: 'drive-attachment',
    content: 'inline',
    propSchema: {}
}

const blockImplentation = {
    render: (props) => {
        return <a href={props.href}>props.title</a>
    }
}

export const DriveAttachmentBlock = createReactBlockSpec(
    blockConfig,
    blockImplentation
);
