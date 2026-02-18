import {
    filterSuggestionItems,
    getDefaultSlashMenuItems,
    insertOrUpdateBlockForSlashMenu,
} from '@blocknote/core/extensions';
import {
    SuggestionMenuController,
    useBlockNoteEditor,
} from '@blocknote/react';
import { useTranslation } from 'react-i18next';

import { createColumnListBlock, isInsideColumn } from './column-layout-block/column-layout-insert-button';
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
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();

    const getItems = async (query: string) => {
        const defaultItems = getDefaultSlashMenuItems(editor);
        const filtered = defaultItems.filter(
            (item) => !HIDDEN_SLASH_MENU_KEYS.has(item.key),
        );

        const customItems = [];

        if ('columnList' in editor.schema.blockSpecs && !isInsideColumn(editor)) {
            customItems.push({
                title: t('2 columns'),
                subtext: t('Image + Text'),
                group: t('Layout'),
                key: 'column_layout',
                onItemClick: () => {
                    insertOrUpdateBlockForSlashMenu(editor, createColumnListBlock())
                },
            });
        }

        return filterSuggestionItems([...filtered, ...customItems], query);
    };

    return (
        <SuggestionMenuController
            triggerCharacter="/"
            getItems={getItems}
        />
    );
};
