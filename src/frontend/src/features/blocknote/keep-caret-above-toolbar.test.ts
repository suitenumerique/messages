/**
 * jsdom has no layout and no ResizeObserver: boxes are stubbed on the
 * elements, and a fake ResizeObserver records what is watched and reports
 * on demand. MutationObserver is jsdom's own.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BlockNoteEditor } from '@blocknote/core';
import { CARET_TOOLBAR_MARGIN, observePinnedBottom, setPinnedBottom } from './keep-caret-above-toolbar';

class FakeResizeObserver {
    static instances: FakeResizeObserver[] = [];
    observed: Element[] = [];
    constructor(private callback: () => void) {
        FakeResizeObserver.instances.push(this);
    }
    observe(element: Element) {
        this.observed.push(element);
        this.callback();
    }
    disconnect() {
        this.observed = [];
    }
}

const stubBox = (element: HTMLElement, { height, position, bottom }: { height: number; position: string; bottom?: string }) => {
    Object.defineProperty(element, 'offsetHeight', { value: height, configurable: true });
    element.style.position = position;
    if (bottom !== undefined) element.style.bottom = bottom;
};

const mountBar = (box: Parameters<typeof stubBox>[1]) => {
    const bar = document.createElement('div');
    stubBox(bar, box);
    document.body.append(bar);
    return bar;
};

const mountDrawer = (bar: HTMLElement, height: number) => {
    const drawer = document.createElement('div');
    drawer.className = 'drawer';
    stubBox(drawer, { height, position: 'fixed', bottom: '0px' });
    bar.append(drawer);
    return drawer;
};

describe('setPinnedBottom', () => {
    const editor = BlockNoteEditor.create();

    it('shrinks the scroll box from the bottom by the pinned height and the margin', () => {
        setPinnedBottom(editor, 40);

        const bottom = 40 + CARET_TOOLBAR_MARGIN;
        expect(editor._tiptapEditor.options.editorProps.scrollThreshold).toEqual({ top: 0, right: 0, bottom, left: 0 });
        expect(editor._tiptapEditor.options.editorProps.scrollMargin).toEqual({ top: 5, right: 5, bottom, left: 5 });
    });

    it('hands the scroll box back to ProseMirror without a pinned surface', () => {
        setPinnedBottom(editor, 40);
        setPinnedBottom(editor, 0);

        expect(editor._tiptapEditor.options.editorProps.scrollThreshold).toBeUndefined();
        expect(editor._tiptapEditor.options.editorProps.scrollMargin).toBeUndefined();
    });

    it('keeps the other editor props', () => {
        const handleDOMEvents = {};
        editor._tiptapEditor.setOptions({ editorProps: { handleDOMEvents } });

        setPinnedBottom(editor, 40);

        expect(editor._tiptapEditor.options.editorProps.handleDOMEvents).toBe(handleDOMEvents);
    });
});

describe('observePinnedBottom', () => {
    const onChange = vi.fn<(height: number) => void>();

    beforeEach(() => {
        vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    });

    afterEach(() => {
        FakeResizeObserver.instances = [];
        document.body.innerHTML = '';
        onChange.mockReset();
        vi.unstubAllGlobals();
    });

    it('reports the bar height plus the offset it sticks at', () => {
        const bar = mountBar({ height: 40, position: 'sticky', bottom: '52px' });

        observePinnedBottom(bar, onChange);

        expect(onChange).toHaveBeenLastCalledWith(92);
    });

    it('reports nothing for a bar laid out in the flow', () => {
        const bar = mountBar({ height: 40, position: 'static' });

        observePinnedBottom(bar, onChange);

        expect(onChange).toHaveBeenLastCalledWith(0);
    });

    it('reports the tallest of the bar and the drawers anchored from it', () => {
        const bar = mountBar({ height: 0, position: 'fixed', bottom: '4px' });
        mountDrawer(bar, 320);

        observePinnedBottom(bar, onChange);

        expect(onChange).toHaveBeenLastCalledWith(320);
    });

    it('watches drawers as they open and close', async () => {
        const bar = mountBar({ height: 40, position: 'fixed', bottom: '4px' });
        observePinnedBottom(bar, onChange);

        const drawer = mountDrawer(bar, 320);
        await vi.waitFor(() => expect(onChange).toHaveBeenLastCalledWith(320));
        expect(FakeResizeObserver.instances[0].observed).toContain(drawer);

        drawer.remove();
        await vi.waitFor(() => expect(onChange).toHaveBeenLastCalledWith(44));
    });

    it('re-measures when the form moves the bar through its inline style', async () => {
        const form = document.createElement('form');
        form.className = 'message-form';
        document.body.append(form);
        const bar = mountBar({ height: 40, position: 'sticky', bottom: '52px' });
        form.append(bar);
        observePinnedBottom(bar, onChange);

        // A taller footer: the form updates --form-footer-height, the
        // stylesheet moves the bar's sticky offset along.
        bar.style.bottom = '72px';
        form.style.setProperty('--form-footer-height', '68px');

        await vi.waitFor(() => expect(onChange).toHaveBeenLastCalledWith(112));
    });

    it('stops reporting once released', async () => {
        const bar = mountBar({ height: 40, position: 'fixed', bottom: '4px' });
        const stop = observePinnedBottom(bar, onChange);
        onChange.mockReset();

        stop();
        mountDrawer(bar, 320);
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(onChange).not.toHaveBeenCalled();
        expect(FakeResizeObserver.instances[0].observed).toEqual([]);
    });
});
