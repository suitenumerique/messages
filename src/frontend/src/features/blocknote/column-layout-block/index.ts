import { createBlockSpecFromTiptapNode } from '@blocknote/core';
import { Node } from '@tiptap/core';

// ---------------------------------------------------------------------------
// Column – a single cell inside a row.
// ProseMirror content = "blockContainer+" so it accepts any BlockNote block
// (paragraph, image, heading, list …) as native children.
// ---------------------------------------------------------------------------

const Column = Node.create({
    name: 'column',
    group: 'bnBlock childContainer',
    content: 'blockContainer+',
    priority: 1,
    defining: true,
    isolating: true,

    addAttributes() {
        return {
            width: {
                default: 1,
                parseHTML: (element: HTMLElement) => {
                    const raw = element.getAttribute('data-width');
                    const width = Number(raw);
                    // fall back to schema default (1)
                    if (raw === null || Number.isNaN(width)) return undefined;
                    return width;
                },
                renderHTML: (attributes: Record<string, unknown>) => ({
                    'data-width': attributes.width,
                    style: `flex-grow: ${attributes.width}`,
                }),
            },
        };
    },

    parseHTML() {
        return [{
            tag: 'div',
            getAttrs: (element) => {
                if (typeof element === 'string') return false;
                if (element.getAttribute('data-node-type') === 'column') return {};
                return false;
            },
        }];
    },

    renderHTML({ HTMLAttributes }) {
        const dom = document.createElement('div');
        dom.className = 'bn-block-column';
        dom.setAttribute('data-node-type', 'column');
        for (const [attr, value] of Object.entries(HTMLAttributes)) {
            dom.setAttribute(attr, value as string);
        }
        return { dom, contentDOM: dom };
    },
});

// ---------------------------------------------------------------------------
// Row – horizontal flex container holding 2+ Column nodes.
// ProseMirror content = "column column+" enforces a minimum of 2 columns.
// ---------------------------------------------------------------------------

const ColumnList = Node.create({
    name: 'columnList',
    group: 'childContainer bnBlock blockGroupChild',
    content: 'column column+',
    priority: 1,
    defining: true,
    isolating: true,

    parseHTML() {
        return [{
            tag: 'div',
            getAttrs: (element) => {
                if (typeof element === 'string') return false;
                if (element.getAttribute('data-node-type') === 'columnList') return {};
                return false;
            },
        }];
    },

    renderHTML({ HTMLAttributes }) {
        const dom = document.createElement('div');
        dom.className = 'bn-block-row';
        dom.setAttribute('data-node-type', 'columnList');
        for (const [attr, value] of Object.entries(HTMLAttributes)) {
            dom.setAttribute(attr, value as string);
        }
        dom.style.display = 'flex';
        return { dom, contentDOM: dom };
    },
});

// ---------------------------------------------------------------------------
// BlockNote block specs built from the raw TipTap nodes
// ---------------------------------------------------------------------------

export const ColumnBlock = createBlockSpecFromTiptapNode(
    { node: Column, type: 'column', content: 'none' },
    { width: { default: 1 } },
);

export const ColumnListBlock = createBlockSpecFromTiptapNode(
    { node: ColumnList, type: 'columnList', content: 'none' },
    {},
);
