import { BlockNoteEditor } from '@blocknote/core';
import { Node as PMNode, Schema } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { EmailExporter } from './email-exporter';
import { createPasteSanitizerPlugin, sanitizeDocumentColors } from './paste-sanitizer';

// Minimal schema mirroring how BlockNote stores colors: as node attributes for
// block props, and as marks carrying a `stringValue` for inline styles.
const schema = new Schema({
    nodes: {
        doc: { content: 'block+' },
        paragraph: {
            group: 'block',
            content: 'inline*',
            attrs: {
                textColor: { default: 'default' },
                backgroundColor: { default: 'default' },
                textAlignment: { default: 'left' },
            },
        },
        text: { group: 'inline' },
    },
    marks: {
        textColor: { attrs: { stringValue: { default: null } } },
        backgroundColor: { attrs: { stringValue: { default: null } } },
        bold: {},
    },
});

const { paragraph } = schema.nodes;
const marks = schema.marks;

const colorMark = (name: 'textColor' | 'backgroundColor', stringValue: string) =>
    marks[name].create({ stringValue });

const docOf = (...paragraphs: PMNode[]) => schema.nodes.doc.create(null, paragraphs);

const stateOf = (doc: PMNode) => EditorState.create({ doc });

const stateWithPlugin = (doc: PMNode) =>
    EditorState.create({ doc, plugins: [createPasteSanitizerPlugin()] });

// A table as a mail client or a spreadsheet puts it on the clipboard: colors
// live on the cells, which BlockNote stores as `tableCell` / `tableHeader` props.
const PASTED_TABLE_HTML =
    '<table><tr>' +
    '<th style="background-color:#4472c4;color:#ffffff">Nom</th>' +
    '</tr><tr>' +
    '<td style="background-color:#d9e2f3">Alice</td>' +
    '</tr></table>';

describe('recognizing palette colors written as raw CSS', () => {
    const nameOfMark = (doc: PMNode) =>
        doc.firstChild!.firstChild!.marks[0]?.attrs.stringValue ?? null;

    const sanitizedMark = (value: string) => {
        const doc = docOf(
            paragraph.create(null, [schema.text('Text', [colorMark('textColor', value)])]),
        );
        const tr = sanitizeDocumentColors(stateOf(doc));
        return nameOfMark(tr ? tr.doc : doc);
    };

    // BlockNote's blue text is #0b6e99 — the value our own exporter emits.
    it.each([
        ['#0b6e99', 'lowercase hex'],
        ['#0B6E99', 'uppercase hex'],
        ['rgb(11, 110, 153)', 'rgb with spaces'],
        ['rgb(11,110,153)', 'rgb without spaces'],
        ['  #0b6e99  ', 'surrounding whitespace'],
        ['rgba(11, 110, 153, 1)', 'rgba fully opaque'],
    ])('names %s back to blue (%s)', (value) => {
        expect(sanitizedMark(value)).toBe('blue');
    });

    it.each([
        ['rgb(11, 110, 154)', 'a near miss'],
        ['rgba(11, 110, 153, 0.5)', 'a translucent palette color'],
        ['hsl(202, 87%, 32%)', 'a notation we do not compare'],
        ['#ddebf1', "blue's background value used as a text color"],
    ])('still drops %s (%s)', (value) => {
        expect(sanitizedMark(value)).toBeNull();
    });

    it('names a background color against the background side of the palette', () => {
        const doc = docOf(
            paragraph.create({ backgroundColor: 'rgb(251, 243, 219)' }, [
                schema.text('Highlighted'),
            ]),
        );

        const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

        expect(cleaned.firstChild!.attrs.backgroundColor).toBe('yellow');
    });

    it('keeps the other marks of a renamed fragment', () => {
        const doc = docOf(
            paragraph.create(null, [
                schema.text('Blue and bold', [
                    marks.bold.create(),
                    colorMark('textColor', '#0b6e99'),
                ]),
            ]),
        );

        const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

        const names = cleaned.firstChild!.firstChild!.marks.map((m) => m.type.name).sort();
        expect(names).toEqual(['bold', 'textColor']);
    });
});

describe('sanitizeDocumentColors', () => {
    describe('inline style marks', () => {
        it('drops a textColor mark whose value is outside the palette', () => {
            const doc = docOf(
                paragraph.create(null, [
                    schema.text('Pasted', [colorMark('textColor', 'rgb(51, 51, 51)')]),
                ]),
            );

            const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

            const child = cleaned.firstChild!.firstChild!;
            expect(child.text).toBe('Pasted');
            expect(child.marks).toHaveLength(0);
        });

        it('drops a backgroundColor mark whose value is outside the palette', () => {
            const doc = docOf(
                paragraph.create(null, [
                    schema.text('Pasted', [colorMark('backgroundColor', '#ffffff')]),
                ]),
            );

            const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

            expect(cleaned.firstChild!.firstChild!.marks).toHaveLength(0);
        });

        it('keeps palette colors so pasting from another composer keeps its formatting', () => {
            const doc = docOf(
                paragraph.create(null, [
                    schema.text('Blue', [colorMark('textColor', 'blue')]),
                ]),
            );

            expect(sanitizeDocumentColors(stateOf(doc))).toBeNull();
        });

        it('keeps non-color marks applied to a stripped fragment', () => {
            const doc = docOf(
                paragraph.create(null, [
                    schema.text('Bold pasted', [
                        marks.bold.create(),
                        colorMark('textColor', 'rgb(0, 0, 0)'),
                    ]),
                ]),
            );

            const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

            const markNames = cleaned.firstChild!.firstChild!.marks.map((m) => m.type.name);
            expect(markNames).toEqual(['bold']);
        });
    });

    describe('block props', () => {
        it('resets off-palette block colors to default', () => {
            const doc = docOf(
                paragraph.create(
                    { textColor: 'rgb(51, 51, 51)', backgroundColor: 'transparent' },
                    [schema.text('Pasted')],
                ),
            );

            const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

            expect(cleaned.firstChild!.attrs).toMatchObject({
                textColor: 'default',
                backgroundColor: 'default',
            });
        });

        it('keeps the other block props untouched', () => {
            const doc = docOf(
                paragraph.create({ textColor: '#123456', textAlignment: 'center' }, [
                    schema.text('Pasted'),
                ]),
            );

            const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

            expect(cleaned.firstChild!.attrs.textAlignment).toBe('center');
        });

        it('keeps palette block colors', () => {
            const doc = docOf(
                paragraph.create({ backgroundColor: 'yellow' }, [
                    schema.text('Highlighted'),
                ]),
            );

            expect(sanitizeDocumentColors(stateOf(doc))).toBeNull();
        });
    });

    it('cleans every block of a multi-block paste without losing content', () => {
        const doc = docOf(
            paragraph.create({ textColor: '#111111' }, [schema.text('First')]),
            paragraph.create(null, [
                schema.text('Second', [colorMark('textColor', '#222222')]),
            ]),
        );

        const cleaned = sanitizeDocumentColors(stateOf(doc))!.doc;

        expect(cleaned.child(0).attrs.textColor).toBe('default');
        expect(cleaned.child(1).firstChild!.marks).toHaveLength(0);
        expect(cleaned.textContent).toBe('FirstSecond');
    });
});

describe('paste sanitizer plugin', () => {
    it('cleans the document when a paste transaction is applied', () => {
        const doc = docOf(
            paragraph.create({ textColor: 'rgb(9, 9, 9)' }, [
                schema.text('Pasted', [colorMark('backgroundColor', '#fefefe')]),
            ]),
        );

        const state = stateWithPlugin(doc);
        const cleaned = state.apply(state.tr.setMeta('paste', true)).doc;

        expect(cleaned.firstChild!.attrs.textColor).toBe('default');
        expect(cleaned.firstChild!.firstChild!.marks).toHaveLength(0);
    });

    it('leaves the document alone when the transaction is not a paste', () => {
        const doc = docOf(
            paragraph.create({ textColor: 'rgb(9, 9, 9)' }, [schema.text('Typed')]),
        );
        const state = stateWithPlugin(doc);

        const after = state.apply(state.tr.insertText('!', 1)).doc;

        expect(after.firstChild!.attrs.textColor).toBe('rgb(9, 9, 9)');
    });
});

// Second line of defense: drafts saved before the sanitizer existed still carry
// off-palette colors, so the exporter must refuse to emit them on its own.
describe('exporting blocks parsed from foreign HTML', () => {
    it('keeps the colors carried by mail clients out of the sent HTML', async () => {
        const editor = BlockNoteEditor.create();
        const blocks = await editor.tryParseHTMLToBlocks(
            '<p style="color: rgb(51, 51, 51); background-color: #ffffff">' +
            'Hello <span style="color:#123456">World</span></p>',
        );

        const html = new EmailExporter().exportBlocks(blocks, null);

        expect(html).toBe('<p>Hello World</p>');
    });

    // A pasted table hits the same trap through its cell props: the editor
    // shows plain cells, so the banded rows and colored headers of the source
    // table must not reappear in the sent mail.
    it('keeps the cell colors of a pasted table out of the sent HTML', async () => {
        const editor = BlockNoteEditor.create();
        const blocks = await editor.tryParseHTMLToBlocks(PASTED_TABLE_HTML);

        const html = new EmailExporter().exportBlocks(blocks, null);

        expect(html).not.toContain('background-color');
        expect(html).not.toContain('rgb(');
        expect(html).toContain('Alice');
    });
});

describe('round-tripping our own exported HTML', () => {
    it('keeps a palette color when a sent mail is pasted back into a reply', async () => {
        const exporter = new EmailExporter();
        const sent = exporter.exportBlocks(
            [
                {
                    id: 'x',
                    type: 'paragraph',
                    props: { textAlignment: 'left', textColor: 'blue', backgroundColor: 'yellow' },
                    content: [{ type: 'text', text: 'Colored', styles: { textColor: 'red' } }],
                    children: [],
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                } as any,
            ],
            null,
        );

        const editor = BlockNoteEditor.create();
        editor.replaceBlocks(editor.document, await editor.tryParseHTMLToBlocks(sent));
        editor.exec((state, dispatch) => {
            dispatch!(sanitizeDocumentColors(state)!);
            return true;
        });

        // Re-exporting the pasted content must yield the very same HTML.
        expect(exporter.exportBlocks(editor.document, null)).toBe(sent);
    });
});

describe('sanitizing a pasted table', () => {
    it('resets the cell colors while preserving the table structure', async () => {
        const editor = BlockNoteEditor.create();
        const blocks = await editor.tryParseHTMLToBlocks(PASTED_TABLE_HTML);
        editor.replaceBlocks(editor.document, blocks);
        const state = editor.prosemirrorState;

        const cleaned = state.apply(sanitizeDocumentColors(state)!).doc;

        const cells: Record<string, unknown>[] = [];
        cleaned.descendants((node) => {
            if (node.type.name === 'tableCell' || node.type.name === 'tableHeader') {
                cells.push(node.attrs);
            }
        });
        expect(cells.length).toBeGreaterThan(0);
        for (const attrs of cells) {
            expect(attrs.backgroundColor).toBe('default');
            expect(attrs.textColor).toBe('default');
            // Layout attributes are none of the sanitizer's business.
            expect(attrs.colspan).toBe(1);
        }
        expect(cleaned.textContent).toContain('Alice');
    });
});
