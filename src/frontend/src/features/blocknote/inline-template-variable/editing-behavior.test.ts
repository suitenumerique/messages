/**
 * Battle-test for the template-variable editing behavior.
 *
 * The token stores its `{value}` label as styled text (BlockNote inline content
 * `"styled"`), so without this extension Backspace/Delete would chew it one
 * character at a time and typing inside would break the token. The logic is
 * pure ProseMirror, so we exercise it against a real schema/state — no TipTap
 * editor, no DOM, no React — by driving the extension through its public
 * surface (the keyboard shortcuts and the ProseMirror plugin it registers).
 */
import { describe, it, expect } from 'vitest';
import { Schema, type Slice } from '@tiptap/pm/model';
import { EditorState, type Plugin, type Transaction } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';
import type { Editor } from '@tiptap/core';
import { TemplateVariableEditingBehavior } from './editing-behavior';

// A minimal schema reproducing the production shape that matters here: an inline
// `template-variable` node holding editable text, surrounded by plain text.
const schema = new Schema({
  nodes: {
    doc: { content: 'block+' },
    paragraph: {
      group: 'block',
      content: 'inline*',
      toDOM: () => ['p', 0],
      parseDOM: [{ tag: 'p' }],
    },
    'template-variable': {
      group: 'inline',
      inline: true,
      content: 'text*',
      toDOM: () => ['span', 0],
      parseDOM: [{ tag: 'span' }],
    },
    text: { group: 'inline' },
  },
});

type Built = { state: EditorState; tokenPos: number; tokenSize: number };

/** Builds `<p>{leading}[{token}]{trailing}</p>` and locates the token node. */
const buildDoc = (leading: string, token: string, trailing: string): Built => {
  const inline = [];
  if (leading) inline.push(schema.text(leading));
  inline.push(schema.node('template-variable', null, [schema.text(token)]));
  if (trailing) inline.push(schema.text(trailing));
  const doc = schema.node('doc', null, [schema.node('paragraph', null, inline)]);
  const state = EditorState.create({ doc, schema });

  let tokenPos = -1;
  let tokenSize = 0;
  doc.descendants((node, pos) => {
    if (node.type.name === 'template-variable') {
      tokenPos = pos;
      tokenSize = node.nodeSize;
    }
  });
  return { state, tokenPos, tokenSize };
};

// --- Reach into the extension through its public config surface -------------
type ShortcutMap = Record<string, (props: { editor: Editor }) => boolean>;
type ExtConfig = {
  addKeyboardShortcuts: () => ShortcutMap;
  addProseMirrorPlugins: () => Plugin[];
};
const config = TemplateVariableEditingBehavior.config as unknown as ExtConfig;
const shortcuts = config.addKeyboardShortcuts();
const [readonlyPlugin] = config.addProseMirrorPlugins();

// ProseMirror types `EditorProps` methods with a `this: Plugin` context, which
// trips TS when calling them as plain functions. Re-type just the handlers we
// drive, decoupled from that `this` binding.
type ReadonlyHandlers = {
  handleTextInput: (view: EditorView, from: number, to: number, text: string) => boolean;
  handlePaste: (view: EditorView, event: ClipboardEvent, slice: Slice) => boolean;
  handleKeyDown: (view: EditorView, event: KeyboardEvent) => boolean;
};
const handlers = readonlyPlugin.props as unknown as ReadonlyHandlers;

/**
 * Fakes the slice of the TipTap editor that `deleteTemplateVariable` reads:
 * a collapsed (or not) selection at `cursorPos` plus a capturing dispatch.
 */
const makeEditor = (state: EditorState, cursorPos: number, empty = true) => {
  let dispatched: Transaction | null = null;
  const $from = state.doc.resolve(cursorPos);
  const editor = {
    state: { selection: { empty, $from }, tr: state.tr },
    view: { dispatch: (tr: Transaction) => { dispatched = tr; } },
  } as unknown as Editor;
  return {
    editor,
    resultText: () => (dispatched ? state.apply(dispatched).doc.textContent : null),
  };
};

const press = (
  key: 'Backspace' | 'Delete',
  b: Built,
  pos: number,
  empty = true,
) => {
  const { editor, resultText } = makeEditor(b.state, pos, empty);
  const handled = shortcuts[key]({ editor });
  return { handled, text: resultText() };
};

describe('TemplateVariableEditingBehavior — atomic deletion', () => {
  it('Backspace removes the whole token when the cursor is inside it', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Backspace', b, b.tokenPos + 2);
    expect(handled).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('Delete removes the whole token when the cursor is inside it', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Delete', b, b.tokenPos + 2);
    expect(handled).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('Backspace removes the token when the cursor sits right after it', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Backspace', b, b.tokenPos + b.tokenSize);
    expect(handled).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('Delete removes the token when the cursor sits right before it', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Delete', b, b.tokenPos);
    expect(handled).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('Backspace at the end of a token-terminated paragraph still removes it', () => {
    const b = buildDoc('Hello ', '{name}', '');
    const { handled, text } = press('Backspace', b, b.tokenPos + b.tokenSize);
    expect(handled).toBe(true);
    expect(text).toBe('Hello ');
  });

  it('Delete after the token is a no-op (the next char is plain text)', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Delete', b, b.tokenPos + b.tokenSize);
    expect(handled).toBe(false);
    expect(text).toBeNull();
  });

  it('Backspace before the token is a no-op (the previous char is plain text)', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Backspace', b, b.tokenPos);
    expect(handled).toBe(false);
    expect(text).toBeNull();
  });

  it('Backspace at the very start of the paragraph is a no-op', () => {
    const b = buildDoc('', '{name}', ' world');
    const { handled, text } = press('Backspace', b, b.tokenPos);
    expect(handled).toBe(false);
    expect(text).toBeNull();
  });

  it('leaves a non-collapsed selection to the default handler', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    // Cursor inside the token, but the selection is a range, not a caret.
    const { handled, text } = press('Backspace', b, b.tokenPos + 2, false);
    expect(handled).toBe(false);
    expect(text).toBeNull();
  });
});

describe('TemplateVariableEditingBehavior — read-only token content', () => {
  const b = buildDoc('Hello ', '{name}', ' world');
  const insidePos = b.tokenPos + 2;
  const outsidePos = 2; // within the leading "Hello " text

  const viewWithDoc = { state: b.state } as unknown as EditorView;
  const viewWithCursor = (pos: number) =>
    ({ state: { selection: { $from: b.state.doc.resolve(pos) } } }) as unknown as EditorView;

  it('blocks text input inside the token', () => {
    expect(handlers.handleTextInput(viewWithDoc, insidePos, insidePos, 'x')).toBe(true);
  });

  it('allows text input outside the token', () => {
    expect(handlers.handleTextInput(viewWithDoc, outsidePos, outsidePos, 'x')).toBe(false);
  });

  it('blocks paste inside the token', () => {
    const noopClipboard = {} as unknown as ClipboardEvent;
    const noopSlice = {} as unknown as Slice;
    expect(handlers.handlePaste(viewWithCursor(insidePos), noopClipboard, noopSlice)).toBe(true);
  });

  it('allows paste outside the token', () => {
    const noopClipboard = {} as unknown as ClipboardEvent;
    const noopSlice = {} as unknown as Slice;
    expect(handlers.handlePaste(viewWithCursor(outsidePos), noopClipboard, noopSlice)).toBe(false);
  });

  it('blocks Enter inside the token', () => {
    const enter = { key: 'Enter' } as unknown as KeyboardEvent;
    expect(handlers.handleKeyDown(viewWithCursor(insidePos), enter)).toBe(true);
  });

  it('allows Enter outside the token', () => {
    const enter = { key: 'Enter' } as unknown as KeyboardEvent;
    expect(handlers.handleKeyDown(viewWithCursor(outsidePos), enter)).toBe(false);
  });

  it('only intercepts Enter, not other keys, inside the token', () => {
    const letter = { key: 'a' } as unknown as KeyboardEvent;
    expect(handlers.handleKeyDown(viewWithCursor(insidePos), letter)).toBe(false);
  });
});

describe('TemplateVariableEditingBehavior — wiring', () => {
  it('binds both Backspace and Delete', () => {
    expect(typeof shortcuts.Backspace).toBe('function');
    expect(typeof shortcuts.Delete).toBe('function');
  });

  it('registers a single ProseMirror plugin exposing the read-only handlers', () => {
    expect(config.addProseMirrorPlugins()).toHaveLength(1);
    expect(typeof readonlyPlugin.props.handleTextInput).toBe('function');
    expect(typeof readonlyPlugin.props.handlePaste).toBe('function');
    expect(typeof readonlyPlugin.props.handleKeyDown).toBe('function');
  });
});
