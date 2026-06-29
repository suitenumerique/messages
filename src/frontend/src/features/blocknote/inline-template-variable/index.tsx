import { createReactInlineContentSpec } from "@blocknote/react";
import React, { useMemo } from "react";
import { useBlockNoteEditor, useComponentsContext } from "@blocknote/react";
import { BlockSchema, StyleSchema, Styles, defaultInlineContentSpecs, InlineContentSchemaFromSpecs } from "@blocknote/core";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { PlaceholderVariable } from "./use-placeholder-variables";

export const TEMPLATE_VARIABLE_TYPE = "template-variable" as const;

export const InlineTemplateVariable = createReactInlineContentSpec(
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
    render: ({ contentRef }) => (
      <span data-inline-type={TEMPLATE_VARIABLE_TYPE} ref={contentRef} />
    ),
  }
);

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

type TemplateVariableSelectorProps = {
  variables: PlaceholderVariable[];
  isLoading: boolean;
}

export const TemplateVariableSelector = ({ variables, isLoading }: TemplateVariableSelectorProps) => {
  const { t } = useTranslation();
  const editor = useBlockNoteEditor<BlockSchema, TemplateVariableInlineContentSchema, StyleSchema>();
  const Components = useComponentsContext()!;
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
