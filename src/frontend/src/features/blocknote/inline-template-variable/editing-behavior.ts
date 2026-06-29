import { Editor, Extension } from "@tiptap/core";
import { ResolvedPos } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { TEMPLATE_VARIABLE_TYPE } from ".";

/** Returns true when the resolved position sits inside a template-variable node. */
const isInsideTemplateVariable = ($pos: ResolvedPos): boolean => {
  for (let depth = $pos.depth; depth > 0; depth--) {
    if ($pos.node(depth).type.name === TEMPLATE_VARIABLE_TYPE) {
      return true;
    }
  }
  return false;
};

/**
 * Deletes a whole template-variable token in a single keystroke.
 *
 * Because the token stores its `{value}` label as styled text, the default
 * Backspace/Delete would otherwise erase it one character at a time. This
 * restores atomic-token behavior: a single keystroke removes the entire
 * variable when the (collapsed) cursor sits inside it or right next to it.
 *
 * @param editor - The TipTap editor instance.
 * @param backward - True for Backspace (look behind), false for Delete (ahead).
 * @returns True when a variable was deleted, so default handling is skipped.
 */
const deleteTemplateVariable = (editor: Editor, backward: boolean): boolean => {
  const { state } = editor;
  const { selection } = state;
  if (!selection.empty) {
    return false;
  }
  const { $from } = selection;

  // Cursor inside the token's text: remove the whole node.
  for (let depth = $from.depth; depth > 0; depth--) {
    if ($from.node(depth).type.name === TEMPLATE_VARIABLE_TYPE) {
      editor.view.dispatch(state.tr.delete($from.before(depth), $from.after(depth)));
      return true;
    }
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
    return [
      new Plugin({
        key: new PluginKey("templateVariableReadonlyContent"),
        props: {
          // Block any text inserted while the cursor sits within a token. Mark
          // commands (bold, color…) dispatch transactions directly and are not
          // routed through these handlers, so styling the token still works.
          handleTextInput: (view, from) =>
            isInsideTemplateVariable(view.state.doc.resolve(from)),
          handlePaste: (view) =>
            isInsideTemplateVariable(view.state.selection.$from),
          handleKeyDown: (view, event) =>
            event.key === "Enter" &&
            isInsideTemplateVariable(view.state.selection.$from),
        },
      }),
    ];
  },
});
