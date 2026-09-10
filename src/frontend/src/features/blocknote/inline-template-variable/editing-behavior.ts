import { Editor, Extension } from "@tiptap/core";
import { Node as PMNode, ResolvedPos } from "@tiptap/pm/model";
import {
  EditorState,
  Plugin,
  PluginKey,
  TextSelection,
  Transaction,
} from "@tiptap/pm/state";
import { TEMPLATE_VARIABLE_TYPE } from ".";

/**
 * Returns the whole-token range when the resolved position sits inside a
 * template-variable node, null otherwise.
 */
const templateVariableRange = (
  $pos: ResolvedPos,
): { from: number; to: number } | null => {
  for (let depth = $pos.depth; depth > 0; depth--) {
    if ($pos.node(depth).type.name === TEMPLATE_VARIABLE_TYPE) {
      return { from: $pos.before(depth), to: $pos.after(depth) };
    }
  }
  return null;
};

/** Returns true when the resolved position sits inside a template-variable node. */
const isInsideTemplateVariable = ($pos: ResolvedPos): boolean =>
  templateVariableRange($pos) !== null;

/**
 * Builds the transaction (or null) restoring the two token invariants:
 *
 * 1. A token's text is its label, always. Chromium binds text typed at the
 *    token boundary (or composed by a mobile IME, e.g. smart punctuation)
 *    into the token's own DOM text node; the DOM observer then re-parses it
 *    as token content. The label is restored and stray characters are
 *    hoisted right after the token, caret behind them. Checked only when
 *    `checkContent` says the doc may have changed.
 * 2. The caret never rests inside a token: its content is read-only, and
 *    with no adjacent text the browser offers no way back out. A collapsed
 *    selection landing inside is pushed out. In `traverse` mode (pure caret
 *    moves) it crosses to the side opposite `cameFrom`, so arrow keys jump
 *    over the token. In `rest` mode (the doc changed: deleting next to the
 *    token makes the browser re-bind the caret inside it) it returns to the
 *    boundary nearest `cameFrom`, so a backspace right after the token
 *    leaves the caret there and the next backspace swallows the token.
 *    Range selections stay untouched: sweeping across the chip is how marks
 *    (bold, color…) get applied to it.
 */
const tokenIntegrityTransaction = (
  state: EditorState,
  cameFrom: number,
  checkContent: boolean,
  mode: "traverse" | "rest",
): Transaction | null => {
  if (checkContent) {
    const broken: { pos: number; node: PMNode }[] = [];
    state.doc.descendants((node, pos) => {
      if (
        node.type.name === TEMPLATE_VARIABLE_TYPE &&
        node.attrs.label &&
        node.textContent !== node.attrs.label
      ) {
        broken.push({ pos, node });
      }
      return true;
    });
    if (broken.length > 0) {
      const tr = state.tr;
      // Descending doc order keeps earlier positions stable.
      for (const { pos, node } of broken.reverse()) {
        const label: string = node.attrs.label;
        const text = node.textContent;
        const marks = node.firstChild?.marks ?? [];
        let prefix = 0;
        while (
          prefix < label.length &&
          prefix < text.length &&
          label[prefix] === text[prefix]
        ) {
          prefix += 1;
        }
        let suffix = 0;
        while (
          suffix < label.length - prefix &&
          suffix < text.length - prefix &&
          label[label.length - 1 - suffix] === text[text.length - 1 - suffix]
        ) {
          suffix += 1;
        }
        // Hoist only clean insertions; any other divergence is simply
        // reverted to the label.
        const isPureInsertion =
          text.slice(0, prefix) + text.slice(text.length - suffix) === label;
        const hoisted = isPureInsertion
          ? text.slice(prefix, text.length - suffix)
          : "";
        if (hoisted) {
          tr.insert(pos + node.nodeSize, state.schema.text(hoisted, marks));
        }
        tr.replaceWith(
          pos + 1,
          pos + 1 + node.content.size,
          state.schema.text(label, marks),
        );
        // Token nodeSize is now label.length + 2 (open + close).
        const caret = pos + label.length + 2 + hoisted.length;
        tr.setSelection(TextSelection.create(tr.doc, caret));
      }
      return tr;
    }
  }

  const { selection } = state;
  if (selection.empty && selection instanceof TextSelection) {
    const range = templateVariableRange(selection.$from);
    if (range) {
      const nearestSide =
        selection.from - range.from <= range.to - selection.from
          ? range.from
          : range.to;
      const target =
        cameFrom <= range.from
          ? mode === "traverse"
            ? range.to
            : range.from
          : cameFrom >= range.to
            ? mode === "traverse"
              ? range.from
              : range.to
            : nearestSide;
      return state.tr.setSelection(TextSelection.create(state.doc, target));
    }
  }
  return null;
};

/**
 * Deletes a whole template-variable token in a single keystroke.
 *
 * Because the token stores its `{value}` label as styled text, the default
 * Backspace/Delete would otherwise erase it one character at a time. This
 * restores atomic-token behavior: a single keystroke removes the entire
 * variable when the (collapsed) cursor sits inside it or right next to it,
 * and a range deletion whose boundary cuts a token swallows that token whole
 * — a partially deleted variable would be a corrupt one.
 *
 * @param editor - The TipTap editor instance.
 * @param backward - True for Backspace (look behind), false for Delete (ahead).
 * @returns True when a variable was deleted, so default handling is skipped.
 */
const deleteTemplateVariable = (editor: Editor, backward: boolean): boolean => {
  const { state } = editor;
  const { selection } = state;
  if (!selection.empty) {
    // Range deletion: expand each boundary that sits inside a token to the
    // token's own bounds, so no token survives half-deleted. Ranges touching
    // no token keep the default handling.
    const fromToken = templateVariableRange(selection.$from);
    const toToken = templateVariableRange(selection.$to);
    if (!fromToken && !toToken) {
      return false;
    }
    editor.view.dispatch(
      state.tr.delete(
        fromToken ? fromToken.from : selection.$from.pos,
        toToken ? toToken.to : selection.$to.pos,
      ),
    );
    return true;
  }
  const { $from } = selection;

  // Cursor inside the token's text: remove the whole node.
  const insideToken = templateVariableRange($from);
  if (insideToken) {
    editor.view.dispatch(state.tr.delete(insideToken.from, insideToken.to));
    return true;
  }

  // Cursor immediately before/after the token: remove it as a whole.
  const adjacent = backward ? $from.nodeBefore : $from.nodeAfter;
  if (adjacent?.type.name === TEMPLATE_VARIABLE_TYPE) {
    const from = backward ? $from.pos - adjacent.nodeSize : $from.pos;
    const to = backward ? $from.pos : $from.pos + adjacent.nodeSize;
    editor.view.dispatch(state.tr.delete(from, to));
    return true;
  }

  return false;
};

/**
 * TipTap extension governing how template-variable tokens behave while editing:
 *
 * - Backspace/Delete remove the whole token at once (atomic deletion).
 * - The token's inner `{value}` text is read-only: typing, pasting or pressing
 *   Enter while the cursor is inside it is blocked, so the token can be styled
 *   as a whole (via mark commands) but never edited into free text.
 */
export const TemplateVariableEditingBehavior = Extension.create({
  name: "templateVariableEditingBehavior",

  // Run before BlockNote's own Backspace/Delete handlers so the whole token is
  // removed instead of letting the default per-character deletion kick in.
  priority: 1000,

  addKeyboardShortcuts() {
    return {
      Backspace: ({ editor }) => deleteTemplateVariable(editor, true),
      Delete: ({ editor }) => deleteTemplateVariable(editor, false),
    };
  },

  addProseMirrorPlugins() {
    const editor = this.editor;
    return [
      new Plugin({
        key: new PluginKey("templateVariableReadonlyContent"),
        props: {
          // Collapsed inserts reported inside a token are boundary artifacts:
          // the caret can never rest inside one (see appendTransaction), but
          // Chromium binds text typed right after the token into its DOM text
          // node. Blocking would swallow the keystroke — let it through, the
          // appendTransaction below hoists it back out after the token. Range
          // replaces touching a token stay blocked: they would corrupt it.
          // Mark commands (bold, color…) dispatch transactions directly and
          // are not routed through these handlers, so styling still works.
          handleTextInput: (view, from, to) =>
            from !== to &&
            (isInsideTemplateVariable(view.state.doc.resolve(from)) ||
              isInsideTemplateVariable(view.state.doc.resolve(to))),
          handlePaste: (view) =>
            isInsideTemplateVariable(view.state.selection.$from) ||
            isInsideTemplateVariable(view.state.selection.$to),
          handleKeyDown: (view, event) =>
            event.key === "Enter" &&
            isInsideTemplateVariable(view.state.selection.$from),
          handleDOMEvents: {
            // Virtual keyboards (GBoard, iOS) delete through beforeinput
            // events instead of Backspace/Delete keydowns, bypassing the
            // keyboard shortcuts above — without this, deleting on mobile
            // eats the token one character at a time. Non-cancelable events
            // (mid-IME-composition) are left to the default path: the DOM
            // edit cannot be prevented, so dispatching our own deletion too
            // would delete twice.
            beforeinput: (view, event) => {
              const backward = event.inputType === "deleteContentBackward";
              if (
                (!backward && event.inputType !== "deleteContentForward") ||
                !event.cancelable
              ) {
                return false;
              }
              if (deleteTemplateVariable(editor, backward)) {
                event.preventDefault();
                return true;
              }
              return false;
            },
            // Edits done entirely within an IME composition (e.g. GBoard
            // backspacing into the token text, which anchors a composition on
            // it) never hit a non-composing transaction, so appendTransaction
            // below stays silent the whole time: the token loses characters
            // and the caret is left stranded inside it. Normalize once the
            // composition settles — deferred a tick so ProseMirror flushes
            // the composed DOM into its state first.
            compositionend: (view) => {
              setTimeout(() => {
                if (view.isDestroyed) return;
                const tr = tokenIntegrityTransaction(
                  view.state,
                  view.state.selection.from,
                  true,
                  "rest",
                );
                if (tr) view.dispatch(tr);
              }, 0);
              return false;
            },
          },
        },
        // Composition (mobile IME) rewrites the DOM before ProseMirror sees
        // anything, so the handlers above cannot protect the token there.
        // Restoring mid-composition would fight the IME, so normalization
        // waits for the composition to settle: appendTransaction covers every
        // non-composing transaction, and compositionend (below, in
        // handleDOMEvents) covers edits whose transactions all happened while
        // composing.
        appendTransaction: (transactions, oldState, newState) => {
          const isComposing = () => {
            // TipTap proxies a missing view and throws on access; before the
            // view is mounted no composition can be ongoing.
            try {
              return editor.view.composing;
            } catch {
              return false;
            }
          };
          if (isComposing()) return null;
          const docChanged = transactions.some(
            (transaction) => transaction.docChanged,
          );
          // Where the caret sat before this batch, in current-doc coordinates
          // — the reference the ejection uses to pick a side.
          const cameFrom = transactions.reduce(
            (pos, transaction) => transaction.mapping.map(pos),
            oldState.selection.from,
          );
          return tokenIntegrityTransaction(
            newState,
            cameFrom,
            docChanged,
            docChanged ? "rest" : "traverse",
          );
        },
      }),
    ];
  },
});
