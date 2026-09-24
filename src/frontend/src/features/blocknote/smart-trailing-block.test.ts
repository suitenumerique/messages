/**
 * The smart trailing block plugin is driven against a minimal schema
 * reproducing BlockNote's document shape (doc > blockGroup > blockContainer >
 * block content), a stub view and plain DOM elements for the pointer
 * listeners: no editor, no layout.
 */
import { describe, it, expect, vi } from 'vitest';
import { Schema, type Node } from '@tiptap/pm/model';
import { EditorState, TextSelection, type Plugin, type PluginView, type Transaction } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';
import { SmartTrailingBlock } from './smart-trailing-block';

const schema = new Schema({
  nodes: {
    doc: { content: 'blockGroup' },
    blockGroup: { content: 'blockContainer+' },
    blockContainer: { content: 'blockContent blockGroup?' },
    paragraph: { group: 'blockContent', content: 'inline*' },
    // Footer blocks are non-selectable atoms (BlockNote `content: "none"`).
    signature: { group: 'blockContent', atom: true, selectable: false },
    text: { group: 'inline' },
  },
});

const container = (content: Node) => schema.node('blockContainer', null, [content]);
const paragraph = (text: string) => schema.node('paragraph', null, text ? [schema.text(text)] : []);
const signature = () => schema.node('signature');

type ExtConfig = { addProseMirrorPlugins: () => Plugin[] };
const config = SmartTrailingBlock.config as unknown as ExtConfig;

type ClickHandler = (view: EditorView, event: MouseEvent) => boolean;

/**
 * Mounts the plugin on a stub view whose DOM mirrors the editor's: a
 * container wrapping the ProseMirror root, which holds a signature and a
 * paragraph element (for the pointer listeners).
 */
const mount = (blocks: Node[], clickPos = 0) => {
  const doc = schema.node('doc', null, [schema.node('blockGroup', null, blocks)]);
  const state = EditorState.create({ doc, schema });

  const host = document.createElement('div');
  const dom = document.createElement('div');
  dom.innerHTML = '<div data-content-type="paragraph"><p>Hello</p></div><div data-content-type="signature"><p>--</p></div>';
  host.appendChild(dom);

  let dispatched = null as Transaction | null;
  const focus = vi.fn();
  const view = {
    state,
    dom,
    dispatch: (tr: Transaction) => { dispatched = tr; },
    posAtCoords: () => ({ pos: clickPos, inside: -1 }),
    focus,
  } as unknown as EditorView;

  const plugin = config.addProseMirrorPlugins()[0];
  const pluginView = plugin.spec.view?.(view) as PluginView;
  const click = plugin.props.handleDOMEvents?.click as unknown as ClickHandler;

  /** Presses a pointer on `selector`; tells whether the mousedown reached ProseMirror's root. */
  const press = (selector: string, pointerType: string) => {
    const target = dom.querySelector(selector)!;
    const pointerDown = new MouseEvent('pointerdown', { bubbles: true });
    Object.defineProperty(pointerDown, 'pointerType', { value: pointerType });
    target.dispatchEvent(pointerDown);

    const reachedRoot = vi.fn();
    dom.addEventListener('mousedown', reachedRoot);
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    dom.removeEventListener('mousedown', reachedRoot);
    return reachedRoot.mock.calls.length > 0;
  };

  const tapClick = () => {
    const handled = click(view, { clientX: 0, clientY: 0 } as MouseEvent);
    return { handled, next: dispatched ? state.apply(dispatched) : state, dispatched };
  };

  return { press, tapClick, focus, destroy: () => pluginView.destroy?.() };
};

// doc(blockGroup(container(paragraph "Hello"), container(signature)))
// "Hello" spans 3..8; the signature node sits at 11 (its container at 10).
const HELLO_END = 8;
const SIGNATURE_POS = 11;

describe('SmartTrailingBlock', () => {
  describe('mousedown on a footer block', () => {
    it('is kept away from ProseMirror on a tap', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())]);

      // Otherwise BlockNote blurs the editor right after the tap's click.
      expect(editor.press('[data-content-type="signature"] p', 'touch')).toBe(false);
      editor.destroy();
    });

    it('still reaches ProseMirror on a mouse click', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())]);

      expect(editor.press('[data-content-type="signature"] p', 'mouse')).toBe(true);
      editor.destroy();
    });

    it('still reaches ProseMirror on a tap in editable content', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())]);

      expect(editor.press('[data-content-type="paragraph"] p', 'touch')).toBe(true);
      editor.destroy();
    });

    it('is no longer intercepted once the plugin view is destroyed', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())]);
      editor.destroy();

      expect(editor.press('[data-content-type="signature"] p', 'touch')).toBe(true);
    });
  });

  describe('click below the editable content', () => {
    it('puts the caret at the end of the text after a tap on a footer block', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())], SIGNATURE_POS);
      editor.press('[data-content-type="signature"] p', 'touch');
      const { handled, dispatched, next } = editor.tapClick();

      expect(handled).toBe(true);
      expect(dispatched?.docChanged).toBe(false);
      expect(next.selection).toBeInstanceOf(TextSelection);
      expect(next.selection.from).toBe(HELLO_END);
      expect(editor.focus).toHaveBeenCalled();
      editor.destroy();
    });

    it('only focuses the editor after a mouse click on a footer block', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())], SIGNATURE_POS);
      editor.press('[data-content-type="signature"] p', 'mouse');
      const { handled, dispatched } = editor.tapClick();

      expect(handled).toBe(true);
      expect(dispatched).toBeNull();
      expect(editor.focus).toHaveBeenCalled();
      editor.destroy();
    });

    it('creates an empty paragraph before the footer when there is no editable block', () => {
      // doc(blockGroup(container(signature))): the signature sits at 2.
      const editor = mount([container(signature())], 2);
      const { handled, next } = editor.tapClick();

      expect(handled).toBe(true);
      expect(next.doc.firstChild?.child(0).firstChild?.type.name).toBe('paragraph');
      expect(next.selection).toBeInstanceOf(TextSelection);
      expect(next.selection.$from.parent.type.name).toBe('paragraph');
      editor.destroy();
    });

    it('leaves clicks above the footer to ProseMirror', () => {
      const editor = mount([container(paragraph('Hello')), container(signature())], 4);
      const { handled, dispatched } = editor.tapClick();

      expect(handled).toBe(false);
      expect(dispatched).toBeNull();
      expect(editor.focus).not.toHaveBeenCalled();
      editor.destroy();
    });
  });
});
