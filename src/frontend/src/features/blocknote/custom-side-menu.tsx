import { SideMenuExtension } from '@blocknote/core/extensions';
import {
    DragHandleButton,
    SideMenu,
    SideMenuController,
    useBlockNoteEditor,
    useExtensionState,
} from '@blocknote/react';

const READ_ONLY_BLOCKS = new Set(['signature', 'quoted-message']);

const FilteredSideMenu = () => {
    const editor = useBlockNoteEditor();
    const block = useExtensionState(SideMenuExtension, {
        editor,
        selector: (state) => state?.block,
    });

    if (!block || READ_ONLY_BLOCKS.has(block.type)) {
        return null;
    }

    return (
        <SideMenu>
            <DragHandleButton />
        </SideMenu>
    );
};

export const CustomSideMenuController = () => (
    <SideMenuController sideMenu={FilteredSideMenu} />
);
