import {
    filterSuggestionItems,
    getDefaultSlashMenuItems,
} from '@blocknote/core/extensions';
import {
    SuggestionMenuController,
    useBlockNoteEditor,
} from '@blocknote/react';

import { HIDDEN_BLOCK_TYPES } from './utils';

const HIDDEN_SLASH_MENU_KEYS = new Set([
    ...HIDDEN_BLOCK_TYPES,
    'code_block',
    'toggle_list',
    'toggle_heading',
    'toggle_heading_2',
    'toggle_heading_3',
    'signature',
    'quoted-message',
]);

export const CustomSlashMenu = () => {
    const editor = useBlockNoteEditor();

    const getItems = async (query: string) => {
        const defaultItems = getDefaultSlashMenuItems(editor);
        const filtered = defaultItems.filter(
            (item) => !HIDDEN_SLASH_MENU_KEYS.has(item.key),
        );
        return filterSuggestionItems(filtered, query);
    };

    return (
        <SuggestionMenuController
            triggerCharacter="/"
            getItems={getItems}
        />
    );
};
