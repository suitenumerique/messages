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
import { describe, it, expect, vi } from 'vitest';
import { Schema, type Slice } from '@tiptap/pm/model';
import { EditorState, TextSelection, type Plugin, type Transaction } from '@tiptap/pm/state';
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
      attrs: { value: { default: '' }, label: { default: '' } },
      toDOM: () => ['span', 0],
      parseDOM: [{ tag: 'span' }],
    },
    text: { group: 'inline' },
  },
});

type Built = { state: EditorState; tokenPos: number; tokenSize: number };

/**
 * Builds `<p>{leading}[{token}]{trailing}</p>` and locates the token node.
 * `label` defaults to the token text; passing a different one simulates a
 * token whose content diverged from its label (IME leakage).
 */
const buildDoc = (
  leading: string,
  token: string,
  trailing: string,
  label = token,
): Built => {
  const inline = [];
  if (leading) inline.push(schema.text(leading));
  inline.push(schema.node('template-variable', { label, value: 'v' }, [schema.text(token)]));
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
  // TipTap invokes this with the extension storage as `this`; the plugin
  // captures `this.editor` for the beforeinput deletion path.
  addProseMirrorPlugins: (this: { editor: Editor }) => Plugin[];
};
const config = TemplateVariableEditingBehavior.config as unknown as ExtConfig;
const shortcuts = config.addKeyboardShortcuts();
const buildPlugin = (editor: Editor) =>
  config.addProseMirrorPlugins.call({ editor })[0];
const readonlyPlugin = buildPlugin({} as Editor);

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
 * a collapsed (or ranged, up to `toPos`) selection plus a capturing dispatch.
 */
const makeEditor = (
  state: EditorState,
  cursorPos: number,
  toPos = cursorPos,
) => {
  let dispatched: Transaction | null = null;
  const $from = state.doc.resolve(cursorPos);
  const $to = state.doc.resolve(toPos);
  const editor = {
    state: { selection: { empty: toPos === cursorPos, $from, $to }, tr: state.tr },
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
  toPos = pos,
) => {
  const { editor, resultText } = makeEditor(b.state, pos, toPos);
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

  it('a range fully inside the token deletes the whole token', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Backspace', b, b.tokenPos + 2, b.tokenPos + 4);
    expect(handled).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('a range entering the token from the left swallows the whole token', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    // From after "He" (pos 3) to inside the token.
    const { handled, text } = press('Backspace', b, 3, b.tokenPos + 2);
    expect(handled).toBe(true);
    expect(text).toBe('He world');
  });

  it('a range leaving the token to the right swallows the whole token', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    // From inside the token to 2 chars into " world": the token and the two
    // selected trailing chars (" w") go together.
    const { handled, text } = press('Backspace', b, b.tokenPos + 2, b.tokenPos + b.tokenSize + 2);
    expect(handled).toBe(true);
    expect(text).toBe('Hello orld');
  });

  it('leaves a plain-text range to the default handler', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, text } = press('Backspace', b, 2, 4);
    expect(handled).toBe(false);
    expect(text).toBeNull();
  });
});

describe('TemplateVariableEditingBehavior — read-only token content', () => {
  const b = buildDoc('Hello ', '{name}', ' world');
  const insidePos = b.tokenPos + 2;
  const outsidePos = 2; // within the leading "Hello " text

  const viewWithDoc = { state: b.state } as unknown as EditorView;
  const viewWithCursor = (pos: number, toPos = pos) =>
    ({
      state: {
        selection: {
          $from: b.state.doc.resolve(pos),
          $to: b.state.doc.resolve(toPos),
        },
      },
    }) as unknown as EditorView;

  it('lets a collapsed insert bound inside the token through (hoisted later)', () => {
    // The caret can never rest inside a token, so a collapsed insert located
    // inside one is Chromium binding boundary-typed text into the token DOM.
    // It goes through and the appendTransaction hoists it back out.
    expect(handlers.handleTextInput(viewWithDoc, insidePos, insidePos, 'x')).toBe(false);
  });

  it('blocks text input over a range that partially covers the token', () => {
    expect(handlers.handleTextInput(viewWithDoc, outsidePos, insidePos, 'x')).toBe(true);
  });

  it('blocks text input over a range inside the token', () => {
    expect(handlers.handleTextInput(viewWithDoc, insidePos, insidePos + 2, 'x')).toBe(true);
  });

  it('allows text input outside the token', () => {
    expect(handlers.handleTextInput(viewWithDoc, outsidePos, outsidePos, 'x')).toBe(false);
  });

  it('blocks paste inside the token', () => {
    const noopClipboard = {} as unknown as ClipboardEvent;
    const noopSlice = {} as unknown as Slice;
    expect(handlers.handlePaste(viewWithCursor(insidePos), noopClipboard, noopSlice)).toBe(true);
  });

  it('blocks paste over a range that partially covers the token', () => {
    const noopClipboard = {} as unknown as ClipboardEvent;
    const noopSlice = {} as unknown as Slice;
    expect(handlers.handlePaste(viewWithCursor(outsidePos, insidePos), noopClipboard, noopSlice)).toBe(true);
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

describe('TemplateVariableEditingBehavior — virtual keyboard deletion (beforeinput)', () => {
  // Virtual keyboards (GBoard, iOS) delete through beforeinput events, not
  // Backspace/Delete keydowns, so the plugin must route them to the same
  // atomic deletion as the keyboard shortcuts.
  const fireBeforeinput = (
    b: Built,
    pos: number,
    inputType: string,
    cancelable = true,
  ) => {
    const { editor, resultText } = makeEditor(b.state, pos);
    const plugin = buildPlugin(editor);
    const { beforeinput } = plugin.props.handleDOMEvents as unknown as {
      beforeinput: (view: EditorView, event: InputEvent) => boolean;
    };
    let prevented = false;
    const event = {
      inputType,
      cancelable,
      preventDefault: () => { prevented = true; },
    } as unknown as InputEvent;
    const handled = beforeinput({} as EditorView, event);
    return { handled, prevented, text: resultText() };
  };

  it('deleteContentBackward inside the token removes it whole', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, prevented, text } = fireBeforeinput(b, b.tokenPos + 2, 'deleteContentBackward');
    expect(handled).toBe(true);
    expect(prevented).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('deleteContentForward right before the token removes it whole', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, prevented, text } = fireBeforeinput(b, b.tokenPos, 'deleteContentForward');
    expect(handled).toBe(true);
    expect(prevented).toBe(true);
    expect(text).toBe('Hello  world');
  });

  it('leaves plain-text deletion to the default path', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, prevented, text } = fireBeforeinput(b, 3, 'deleteContentBackward');
    expect(handled).toBe(false);
    expect(prevented).toBe(false);
    expect(text).toBeNull();
  });

  it('leaves non-cancelable events to the default path (no double deletion)', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, prevented, text } = fireBeforeinput(b, b.tokenPos + 2, 'deleteContentBackward', false);
    expect(handled).toBe(false);
    expect(prevented).toBe(false);
    expect(text).toBeNull();
  });

  it('ignores non-deletion input types', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const { handled, prevented, text } = fireBeforeinput(b, b.tokenPos + 2, 'insertText');
    expect(handled).toBe(false);
    expect(prevented).toBe(false);
    expect(text).toBeNull();
  });
});

describe('TemplateVariableEditingBehavior — token integrity (appendTransaction)', () => {
  type AppendTx = (
    trs: readonly Transaction[],
    oldState: EditorState,
    newState: EditorState,
  ) => Transaction | null | undefined;
  const appendTx = buildPlugin({} as Editor).spec.appendTransaction as unknown as AppendTx;
  const identityMapping = { map: (pos: number) => pos };
  const docChanged = [{ docChanged: true, mapping: identityMapping }] as unknown as Transaction[];
  const selectionOnly = [{ docChanged: false, mapping: identityMapping }] as unknown as Transaction[];

  const withSelection = (state: EditorState, from: number, to = from) =>
    state.apply(
      state.tr.setSelection(TextSelection.create(state.doc, from, to)),
    );

  const tokenOf = (state: EditorState): { text: string; pos: number } | null => {
    let found: { text: string; pos: number } | null = null;
    state.doc.descendants((node, pos) => {
      if (node.type.name === 'template-variable') found = { text: node.textContent, pos };
    });
    return found;
  };

  it('restores the label and hoists stray characters after the token', () => {
    // The IME leaked a "!" into the token: "{name}" became "{name}!".
    const b = buildDoc('Hello ', '{name}!', ' world', '{name}');
    const tr = appendTx(docChanged, b.state, b.state);
    expect(tr).toBeTruthy();
    const result = b.state.apply(tr!);
    expect(tokenOf(result)?.text).toBe('{name}');
    expect(result.doc.textContent).toBe('Hello {name}! world');
    // Caret lands after the hoisted "!" so typing continues outside the token.
    expect(result.selection.from).toBe(b.tokenPos + '{name}'.length + 2 + 1);
  });

  it('reverts any non-insertion divergence to the label', () => {
    // Characters were somehow replaced inside the token: no clean hoist.
    const b = buildDoc('Hello ', '{nXme}', ' world', '{name}');
    const tr = appendTx(docChanged, b.state, b.state);
    expect(tr).toBeTruthy();
    const result = b.state.apply(tr!);
    expect(tokenOf(result)?.text).toBe('{name}');
    expect(result.doc.textContent).toBe('Hello {name} world');
  });

  it('leaves a doc whose tokens match their label untouched', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    expect(appendTx(docChanged, b.state, b.state)).toBeFalsy();
  });

  it('pushes a caret entering from the left out to the right side', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const oldState = withSelection(b.state, b.tokenPos);
    const newState = withSelection(b.state, b.tokenPos + 2);
    const tr = appendTx(selectionOnly, oldState, newState);
    expect(tr).toBeTruthy();
    expect(tr!.selection.from).toBe(b.tokenPos + b.tokenSize);
  });

  it('pushes a caret entering from the right out to the left side', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const oldState = withSelection(b.state, b.tokenPos + b.tokenSize);
    const newState = withSelection(b.state, b.tokenPos + 2);
    const tr = appendTx(selectionOnly, oldState, newState);
    expect(tr).toBeTruthy();
    expect(tr!.selection.from).toBe(b.tokenPos);
  });

  it('frees the caret even when the token has no surrounding text', () => {
    const b = buildDoc('', '{name}', '');
    const oldState = withSelection(b.state, b.tokenPos);
    const newState = withSelection(b.state, b.tokenPos + 1);
    const tr = appendTx(selectionOnly, oldState, newState);
    expect(tr).toBeTruthy();
    expect(tr!.selection.from).toBe(b.tokenPos + b.tokenSize);
  });

  it('rests the caret after the token when a deletion rebound it inside', () => {
    // Backspacing the char right after the token: the browser re-binds the
    // caret inside the token DOM. The caret must come back to rest after the
    // token — not cross to the other side — so the next backspace swallows
    // the token itself.
    const b = buildDoc('Hello ', '{name}', ' world');
    const oldState = withSelection(b.state, b.tokenPos + b.tokenSize + 1);
    const newState = withSelection(b.state, b.tokenPos + b.tokenSize - 1);
    const tr = appendTx(docChanged, oldState, newState);
    expect(tr).toBeTruthy();
    expect(tr!.selection.from).toBe(b.tokenPos + b.tokenSize);
  });

  it('rests the caret before the token when a forward deletion rebound it inside', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const oldState = withSelection(b.state, b.tokenPos - 1);
    const newState = withSelection(b.state, b.tokenPos + 1);
    const tr = appendTx(docChanged, oldState, newState);
    expect(tr).toBeTruthy();
    expect(tr!.selection.from).toBe(b.tokenPos);
  });

  it('leaves range selections inside the token alone (styling sweeps)', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const oldState = withSelection(b.state, b.tokenPos);
    const newState = withSelection(b.state, b.tokenPos + 2, b.tokenPos + 4);
    expect(appendTx(selectionOnly, oldState, newState)).toBeFalsy();
  });

  it('leaves a caret outside any token alone', () => {
    const b = buildDoc('Hello ', '{name}', ' world');
    const oldState = withSelection(b.state, 2);
    const newState = withSelection(b.state, 3);
    expect(appendTx(selectionOnly, oldState, newState)).toBeFalsy();
  });

  it('repairs IME-composed damage once the composition ends', () => {
    // Edits done entirely mid-composition never reach appendTransaction; the
    // compositionend handler runs the same normalization, one tick later.
    vi.useFakeTimers();
    try {
      const b = buildDoc('Hello ', '{name}!', ' world', '{name}');
      let dispatched: Transaction | null = null;
      const view = {
        isDestroyed: false,
        state: b.state,
        dispatch: (tr: Transaction) => { dispatched = tr; },
      };
      const { compositionend } = buildPlugin({} as Editor).props
        .handleDOMEvents as unknown as {
        compositionend: (view: unknown, event: CompositionEvent) => boolean;
      };
      compositionend(view, {} as CompositionEvent);
      expect(dispatched).toBeNull();
      vi.runAllTimers();
      expect(dispatched).toBeTruthy();
      const result = b.state.apply(dispatched!);
      expect(result.doc.textContent).toBe('Hello {name}! world');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('TemplateVariableEditingBehavior — wiring', () => {
  it('binds both Backspace and Delete', () => {
    expect(typeof shortcuts.Backspace).toBe('function');
    expect(typeof shortcuts.Delete).toBe('function');
  });

  it('registers a single ProseMirror plugin exposing the read-only handlers', () => {
    expect(config.addProseMirrorPlugins.call({ editor: {} as Editor })).toHaveLength(1);
    expect(typeof readonlyPlugin.props.handleTextInput).toBe('function');
    expect(typeof readonlyPlugin.props.handlePaste).toBe('function');
    expect(typeof readonlyPlugin.props.handleKeyDown).toBe('function');
    const domHandlers = readonlyPlugin.props.handleDOMEvents as unknown as Record<string, unknown>;
    expect(typeof domHandlers.beforeinput).toBe('function');
  });
});
