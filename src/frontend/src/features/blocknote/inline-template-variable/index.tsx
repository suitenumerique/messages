import { createReactInlineContentSpec } from "@blocknote/react";
import React, { useMemo } from "react";
import { useBlockNoteEditor, useComponentsContext } from "@blocknote/react";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { PlaceholdersRetrieve200 } from "@/features/api/gen";
import { SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema } from "@/features/signatures/components/signature-composer";
import { useTranslation } from "react-i18next";

type TemplateVariableSelectorProps = {
  variables: PlaceholdersRetrieve200;
  isLoading: boolean;
}

export const TemplateVariableSelector = ({ variables, isLoading }: TemplateVariableSelectorProps) => {
  const { t } = useTranslation();
  const editor = useBlockNoteEditor<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>();
  const Components = useComponentsContext()!;
  const variableItems = useMemo(() => {
    if (!variables) return [];
    return Object.entries(variables).map(([value, label]) => ({
      text: label,
      icon: null,
      isSelected: false,
      onClick: () => {
        editor.insertInlineContent([{ type: "template-variable", props: { label, value } }, " "]);
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

  if (!variables) {
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


export const InlineTemplateVariable = createReactInlineContentSpec(
  {
    type: "template-variable",
    content: "none",
    propSchema: {
      value: { default: "" },
      label: { default: "" },
    },
  },
  {
    render: ({ inlineContent: { props } }) => {
      return (
        // TODO : Find a way to display variable name
        // and (de)serialize this inline content during export and parsing
        <span data-inline-type="template-variable">
          {`{${props.value}}`}
        </span>
      );
    },
  }
);




