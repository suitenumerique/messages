import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { EditorState, Transaction } from '@tiptap/pm/state';
import { isBlockNoteColor, matchBlockNoteColorName } from './colors';

/**
 * Color-carrying names, shared by the block props (node attributes) and the
 * inline style marks — BlockNote uses the same names for both — mapped to the
 * side of the palette they must be matched against.
 */
const COLOR_VARIANTS: Record<string, 'text' | 'background'> = {
    textColor: 'text',
    backgroundColor: 'background',
};

const COLOR_NAMES = Object.keys(COLOR_VARIANTS);

/**
 * Value BlockNote uses to mean "no color", i.e. the one it omits when rendering.
 */
const DEFAULT_COLOR = 'default';

/**
 * Builds the transaction normalizing every color the editor cannot render:
 * palette colors written as raw CSS are named back, the rest is reset.
 *
 * The whole document is swept rather than only the inserted range: the sweep
 * enforces the invariant, so anything already clean is left untouched and the
 * few off-palette values left over in an older draft get fixed on the way.
 *
 * @returns the cleaning transaction, or `null` when the document is already clean
 */
export function sanitizeDocumentColors(state: EditorState): Transaction | null {
    const tr = state.tr;
    let changed = false;

    state.doc.descendants((node, pos) => {
        for (const name of COLOR_NAMES) {
            if (!(name in node.attrs) || isBlockNoteColor(node.attrs[name])) continue;
            const paletteName = matchBlockNoteColorName(node.attrs[name], COLOR_VARIANTS[name]);
            tr.setNodeAttribute(pos, name, paletteName ?? DEFAULT_COLOR);
            changed = true;
        }
        for (const mark of node.marks) {
            const variant = COLOR_VARIANTS[mark.type.name];
            if (!variant || isBlockNoteColor(mark.attrs.stringValue)) continue;

            const paletteName = matchBlockNoteColorName(mark.attrs.stringValue, variant);
            const end = pos + node.nodeSize;
            tr.removeMark(pos, end, mark);
            if (paletteName) {
                tr.addMark(pos, end, mark.type.create({ ...mark.attrs, stringValue: paletteName }));
            }
            changed = true;
        }
    });

    return changed ? tr : null;
}

/**
 * Cleans up after a paste or an external drop, once ProseMirror has inserted
 * the content.
 *
 * We deliberately do not hook `transformPasted`: ProseMirror only ever calls
 * the first handler it finds, and BlockNote already owns it to regenerate the
 * ids of pasted blocks.
 */
export const createPasteSanitizerPlugin = () =>
    new Plugin({
        key: new PluginKey('pasteColorSanitizer'),
        appendTransaction: (transactions, _oldState, newState) => {
            const inserted = transactions.some(
                (tr) => tr.getMeta('paste') || tr.getMeta('uiEvent') === 'drop',
            );
            return inserted ? sanitizeDocumentColors(newState) : null;
        },
    });

/**
 * Drops the colors BlockNote infers from pasted HTML but cannot render.
 *
 * BlockNote maps any `style="color: …"` / `style="background-color: …"` found
 * in the clipboard onto its `textColor` / `backgroundColor` props and marks,
 * whatever the value. Its stylesheet only renders the nine named palette
 * colors, so a `rgb(51, 51, 51)` inherited from Word or Gmail stays invisible
 * while composing — but the email exporter turns it back into an inline style,
 * littering the sent HTML with markup that spam filters read as
 * machine-generated. Since the user never sees those colors, dropping them on
 * paste keeps the composer WYSIWYG and the stored draft clean.
 *
 * Palette colors are kept rather than dropped: they are recognized even when
 * the clipboard hands them back as raw CSS — our own exporter writes them that
 * way (`color:#0b6e99`) — so replying to a Messages mail keeps its formatting.
 */
export const PasteColorSanitizer = Extension.create({
    name: 'pasteColorSanitizer',
    addProseMirrorPlugins() {
        return [createPasteSanitizerPlugin()];
    },
});
