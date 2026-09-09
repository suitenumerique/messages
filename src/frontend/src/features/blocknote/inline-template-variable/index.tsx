import { createReactInlineContentSpec } from "@blocknote/react";
import React, { useMemo } from "react";
import { useBlockNoteEditor, useComponentsContext } from "@blocknote/react";
import { BlockSchema, StyleSchema, Styles, defaultInlineContentSpecs, InlineContentSchemaFromSpecs } from "@blocknote/core";
import { createPortal } from "react-dom";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { MobileToolbarButton } from "@/features/blocknote/mobile-toolbar/buttons";
import { useMobileToolbarDrawer } from "@/features/blocknote/mobile-toolbar/drawer-context";
import { Drawer } from "@/features/ui/components/drawer";
import { PlaceholderVariable } from "./use-placeholder-variables";

export const TEMPLATE_VARIABLE_TYPE = "template-variable" as const;

const InlineTemplateVariableSpec = createReactInlineContentSpec(
  {
    type: TEMPLATE_VARIABLE_TYPE,
    // "styled" (instead of "none") so the `{value}` token is stored as styled
    // text. This lets the standard formatting toolbar (bold, italic, color…)
    // apply marks that BlockNote persists in the block JSON — a "none" inline
    // content drops them on serialization, losing every style at render time.
    content: "styled",
    propSchema: {
      value: { default: "" },
      label: { default: "" },
    },
  },
  {
    // The chip DOM must stay editable: the model allows the caret inside the
    // token (styled content), and a contenteditable=false island leaves that
    // caret with no DOM home — invisible cursor on desktop, dismissed
    // keyboard on mobile. spellCheck off discourages mobile IMEs from
    // treating the label as a composable word (their compositions bypass the
    // editing guards and have to be repaired after the fact).
    render: ({ contentRef }) => (
      <span
        data-inline-type={TEMPLATE_VARIABLE_TYPE}
        spellCheck={false}
        ref={contentRef}
      />
    ),
  }
);

export const InlineTemplateVariable = {
  ...InlineTemplateVariableSpec,
  implementation: {
    ...InlineTemplateVariableSpec.implementation,
    node: InlineTemplateVariableSpec.implementation.node.extend({
      // A token can only ever hold styled text — "styled" maps to "inline*",
      // which would let a token nest inside another one on DOM re-parse.
      content: "text*",
      // TipTap's default NodeView ignoreMutation has an iOS/Android-only
      // branch (user-agent sniffed) that lets childList mutations inside the
      // NodeView DOM through to ProseMirror. The React NodeView mounts its
      // wrappers asynchronously, so on mobile that very mount is seen as a
      // foreign mutation: ProseMirror redraws the node view, React remounts
      // it, and the app hangs in an endless synchronous commit loop (the
      // "insert a variable freezes the app" bug). React owns everything
      // inside the chip and its text only changes through transactions, so
      // every internal mutation is safe to ignore; selection mutations keep
      // the default behavior (node selection still works).
      addNodeView() {
        const createParentNodeView = this.parent?.();
        if (!createParentNodeView) return null;
        return (props) => {
          const nodeView = createParentNodeView(props);
          if (nodeView && typeof nodeView === "object") {
            const defaultIgnoreMutation =
              nodeView.ignoreMutation?.bind(nodeView);
            nodeView.ignoreMutation = (mutation) =>
              mutation.type === "selection"
                ? (defaultIgnoreMutation?.(mutation) ?? false)
                : true;
          }
          return nodeView;
        };
      },
    }),
  },
} as typeof InlineTemplateVariableSpec;

/**
 * Builds the inline content inserted when picking a variable: the token itself
 * followed by a trailing space.
 *
 * The token displays the human `label` (e.g. "Nom de l'expéditeur") as its
 * styled content so it can be formatted, while `value`/`label` stay in props —
 * `value` being the canonical slug used for resolution and email export. Both
 * the token and the trailing space are seeded with the provided styles —
 * typically the active styles at the cursor — so the variable inherits the
 * surrounding formatting and the text typed right after it keeps those styles
 * (the caret lands after the styled space and inherits its marks).
 *
 * @param variable - The picked variable (`value` slug and display `label`).
 * @param styles - Styles to seed the token and trailing space with.
 */
export const buildTemplateVariableInsertion = <S extends StyleSchema>(
  { value, label }: PlaceholderVariable,
  styles: Styles<S> = {} as Styles<S>,
) => [
  {
    type: TEMPLATE_VARIABLE_TYPE,
    props: { value, label },
    content: [{ type: "text" as const, text: label, styles }],
  },
  { type: "text" as const, text: " ", styles },
];

type TemplateVariableInlineContentSchema = InlineContentSchemaFromSpecs<
  typeof defaultInlineContentSpecs & { [TEMPLATE_VARIABLE_TYPE]: typeof InlineTemplateVariable }
>;

const VARIABLES_DRAWER_ID = "template-variables";

type TemplateVariableSelectorProps = {
  variables: PlaceholderVariable[];
  isLoading: boolean;
}

export const TemplateVariableSelector = ({ variables, isLoading }: TemplateVariableSelectorProps) => {
  const { t } = useTranslation();
  const editor = useBlockNoteEditor<BlockSchema, TemplateVariableInlineContentSchema, StyleSchema>();
  const Components = useComponentsContext()!;
  // Non-null when rendered inside the mobile toolbar: variables are then
  // picked from a bottom drawer instead of the desktop inline select.
  const mobileDrawer = useMobileToolbarDrawer();
  const variableItems = useMemo(() => {
    return variables.map(({ value, label }) => ({
      text: label,
      icon: null,
      isSelected: false,
      onClick: () => {
        editor.insertInlineContent(buildTemplateVariableInsertion({ value, label }, editor.getActiveStyles()));
      }
    }));
  }, [editor, variables]);

  if (isLoading) {
    if (mobileDrawer) {
      return (
        <MobileToolbarButton
          icon={<Spinner size="sm" />}
          label={t("Loading variables...")}
          isDisabled
          onClick={() => {}}
        />
      );
    }
    return (
      <Components.FormattingToolbar.Button
        icon={<Spinner size="sm" />}
        isDisabled={true}
        label={t("Loading variables...")}
        mainTooltip={t("Loading variables...")}
      />
    );
  }

  if (!variables.length) {
    return null;
  }

  if (mobileDrawer) {
    return (
      <>
        <MobileToolbarButton
          icon={<Icon name="space_bar" size={IconSize.MEDIUM} />}
          label={t("Variables")}
          isActive={mobileDrawer.openId === VARIABLES_DRAWER_ID}
          onClick={() => mobileDrawer.open(VARIABLES_DRAWER_ID)}
        />
        {mobileDrawer.openId === VARIABLES_DRAWER_ID &&
          mobileDrawer.slot &&
          createPortal(
            <Drawer title={t("Variables")} onClose={mobileDrawer.close}>
              <div className="drawer-list">
                {variableItems.map((item) => (
                  <button
                    type="button"
                    key={item.text}
                    className="drawer-list__item"
                    onClick={() => {
                      // Close first: the caret insertion must happen in a
                      // focused editor. Inserting inline content while the
                      // editor is blurred with inputmode="none" and focusing
                      // right after makes the Android IME restart composition
                      // on the freshly-mutated text and hang the webview.
                      mobileDrawer.close();
                      item.onClick();
                    }}
                  >
                    <Icon name="space_bar" size={IconSize.MEDIUM} />
                    <span className="drawer-list__item-label">
                      {item.text}
                    </span>
                  </button>
                ))}
              </div>
            </Drawer>,
            mobileDrawer.slot,
          )}
      </>
    );
  }

  return (
    <Components.FormattingToolbar.Select
      key={"templateVariableSelector"}
      items={[
        {
          text: t("Variables"),
          isSelected: true,
          isDisabled: true,
          icon: <Icon name="space_bar" size={IconSize.SMALL} />,
          onClick: () => {}
        },
        ...variableItems,
      ]}
    />
  );
}
